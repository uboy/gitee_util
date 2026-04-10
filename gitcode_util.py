#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import requests
import subprocess
import base64
import json
from prompt_toolkit import prompt
import re
import html
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any
from pathlib import Path
import time
import threading
import itertools
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config_bootstrap import ensure_provider_config, maybe_refresh_provider_token

# see https://docs.gitcode.com/docs/apis/
# test link https://gitcode.com/api/v5/repos/openharmony/arkui_ace_engine/pulls?base=OpenHarmony_feature_20250702&state=open&id=69593

# Global variables
# ANSI цвета
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

USE_COLOR = sys.stdout.isatty()
def colorize(text, color):
    return f"{color}{text}{RESET}" if USE_COLOR else text

SUCCESS_LABELS = {"静态检查成功", "dco检查成功", "编译成功", "冒烟测试成功"}
FAIL_LABELS = {"waiting_for_review", "waiting_on_author", "静态检查失败", "编译失败", "冒烟测试失败", "dco检查失败"}
DEFAULT_LIST_PR_REPO = "openharmony/arkui_ace_engine"


class _Spinner:
    """Simple TTY spinner that runs in a background thread."""
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = ""):
        self._msg = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(self._FRAMES):
            if self._stop.is_set():
                break
            sys.stderr.write(f"\r{frame} {self._msg}  ")
            sys.stderr.flush()
            self._stop.wait(0.1)
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def update(self, message: str):
        self._msg = message

    def __enter__(self):
        if sys.stderr.isatty():
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)

BASE_DIR = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
# --------------------------------------------------------------------
# Config / utils
# --------------------------------------------------------------------
def load_config():
    return ensure_provider_config("gitcode")


def get_owner_config(client, owner: str, repo: str, ref: str = "master") -> Dict:
    """
    Загружает owner_config.json с кэшированием на неделю.
    """
    cache_file = CACHE_DIR / f"owner_config_{owner}_{repo}_{ref}.json"
    max_age = 7 * 24 * 60 * 60  # 1 неделя в секундах

    if cache_file.exists():
        mtime = cache_file.stat().st_mtime
        if (time.time() - mtime) < max_age:
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass  # если повреждён, просто перекачаем

    # если нет кэша или он старый → качаем заново
    content = client.get_file_from_repo(owner, repo, ".gitcode/owner_config.json", ref=ref)
    if not content:
        return {}

    try:
        data = json.loads(content)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except json.JSONDecodeError:
        return {}


def strip_html_tags(text: str) -> str:
    """Удаляет html-теги и приводит к читаемому виду"""
    if not text:
        return ""
    text = html.unescape(text)
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(separator="\n").strip()


# --------------------------------------------------------------------
# Gitee client (все запросы проходят через safe_request)
# --------------------------------------------------------------------
class GiteeClient:
    def __init__(self, base_url: str, token: str, members: str, config_path: str):
        # base_url like "https://gitcode.com"
        self.api_base = f"{base_url}/api/v5"
        self.session = requests.Session()
        # use access_token param to be compatible with Gitcode API v5
        self.session.params = {"access_token": token}
        # members file
        self.members = members
        self.config_path = config_path
        self._auth_retry_used = False
        self._cache: Dict[Any, Any] = {}  # 🔒 Кэш для шаблонов и других ресурсов
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://",  HTTPAdapter(max_retries=retry))

    def safe_request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """Выполняет запрос и печатает понятную ошибку при неудаче."""
        try:
            r = self.session.request(method, url, timeout=(5, 10), **kwargs)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            text = getattr(e.response, "text", "")
            if status in (401, 403) and not self._auth_retry_used:
                self._auth_retry_used = True
                refreshed_token = maybe_refresh_provider_token("gitcode", self.config_path)
                if refreshed_token:
                    self.session.params = {"access_token": refreshed_token}
                    return self.safe_request(method, url, **kwargs)
            print(f"❌ Gitcode API error: {status}\n{text}\n{url}")
            return None
        except requests.RequestException as e:
            print(f"❌ Network error: {e}")
            return None


    # ---- templates/files ----
    def get_file_from_repo(self, owner: str, repo: str, path: str, ref: str = "master") -> Optional[str]:
        """
        Возвращает содержимое файла (или спрашивает какой файл взять, если их несколько).
        """
        cache_key = ("file", owner, repo, path, ref)
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.api_base}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        r = self.safe_request("GET", url)
        if r is None:
            return None

        data = r.json()

        # если это директория
        if isinstance(data, list):
            candidates = [f for f in data if f["name"].lower().startswith(path.split("/")[-1].split("_")[0].lower())]
            if not candidates:
                return None
            if len(candidates) == 1:
                file_url = candidates[0]["download_url"]
            else:
                print("Несколько шаблонов найдено:")
                for i, f in enumerate(candidates, 1):
                    print(f"  {i}. {f['name']}")
                choice = int(input("Выберите номер файла > ")) - 1
                file_url = candidates[choice]["download_url"]

            r_file = self.safe_request("GET", file_url)
            if r_file is None:
                return None
            decoded = r_file.text
        else:  # если это файл
            content = data.get("content")
            if not content:
                return None
            decoded = base64.b64decode(content).decode("utf-8")

        self._cache[cache_key] = decoded
        return decoded


    def get_issue_templates(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Вернуть список файлов в .gitcode/ISSUE_TEMPLATE (если есть). Кэшируем."""
        cache_key = ("templates", owner, repo, "issue")
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.api_base}/repos/{owner}/{repo}/contents/.gitcode/ISSUE_TEMPLATE"
        r = self.safe_request("GET", url)
        if r is None:
            return []
        if r.status_code != 200:
            return []
        res = r.json()
        self._cache[cache_key] = res
        return res

    def get_labels(self, owner: str, repo: str) -> List[str]:
        cache_key = ("labels", owner, repo)
        if cache_key in self._cache:
            return self._cache[cache_key]
        labels, page = [], 1
        while True:
            url = f"{self.api_base}/repos/{owner}/{repo}/labels?per_page=100&page={page}"
            r = self.safe_request("GET", url)
            if not r: break
            page_data = r.json() or []
            labels.extend([lbl.get("name") for lbl in page_data if "name" in lbl])
            if len(page_data) < 100: break
            page += 1
        labels = list(dict.fromkeys(labels))
        self._cache[cache_key] = labels
        return labels

    # ---- issues/pulls/comments ----
    def create_issue(self, owner: str, repo: str, title: str, body: str, labels: Optional[List[str]] = None) -> Optional[Dict]:
        if labels is None:
            labels = []
        url = f"{self.api_base}/repos/{owner}/issues"
        data = {
            "title": title,
            "body": body,
            "labels": labels,
            "repo": repo
        }
        r = self.safe_request("POST", url, json=data)
        if r is None:
            return None
        return r.json()

    def create_pull_request(self, owner: str, repo: str, title: str, body: str, head: str, base: str) -> Optional[Dict]:
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls"
        data = {
            "title": title,
            "body": body or "",
            "head": head,  # e.g. "owner:feature_branch"
            "base": base,
            "draft": True,
            "squash": True,
            "close_related_issue": True
        }
        # show pretty payload for debugging
        print("📤 Sending pull request with data:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        r = self.safe_request("POST", url, json=data)
        if r is None:
            return None
        return r.json()

    def list_pull_requests(self, owner: str, repo: str, state: str = "open", author: Optional[str] = None, per_page: int = 50, max_results: int = 0) -> List[Dict]:
        """
        Собирает PR'ы с пагинацией. Возвращает массив PR.
        max_results > 0 — остановить сбор как только набрали достаточно.
        """
        collected = []
        page = 1
        fetch_size = per_page if max_results == 0 else min(per_page, max_results)
        while True:
            url = f"{self.api_base}/repos/{owner}/{repo}/pulls?state={state}&per_page={fetch_size}&page={page}"
            if author:
                url += f"&author={author}"
            r = self.safe_request("GET", url)
            if r is None:
                break
            page_data = r.json()
            if not page_data:
                break
            collected.extend(page_data)
            if max_results > 0 and len(collected) >= max_results:
                break
            # Если меньше, чем fetch_size — конец
            if len(page_data) < fetch_size:
                break
            page += 1
        return collected

    def list_issues(self, owner: str, repo: str, state: str = "open", per_page: int = 50, max_results: int = 100) -> List[Dict]:
        collected = []
        page = 1
        fetch_size = per_page if max_results == 0 else min(per_page, max_results)
        while True:
            url = f"{self.api_base}/repos/{owner}/{repo}/issues?state={state}&per_page={fetch_size}&page={page}"
            r = self.safe_request("GET", url)
            if r is None:
                break
            page_data = r.json()
            if not page_data:
                break
            collected.extend(page_data)
            if max_results > 0 and len(collected) >= max_results:
                break
            if len(page_data) < fetch_size:
                break
            page += 1
        return collected

    def comment_pull_request(self, owner: str, repo: str, pr_number: int, comment: str) -> Optional[Dict]:
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        data = {"body": comment}
        r = self.safe_request("POST", url, json=data)
        if r is None:
            return None
        return r.json()

    def validate_repository(self, owner: str, repo: str) -> bool:
        url = f"{self.api_base}/repos/{owner}/{repo}"
        r = self.safe_request("GET", url)
        return bool(r and r.ok)

    def validate_branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        url = f"{self.api_base}/repos/{owner}/{repo}/branches/{branch}"
        r = self.safe_request("GET", url)
        return bool(r and r.ok)

    def get_pull_request_comments(self, owner: str, repo: str, pr_id: int) -> List[Dict]:
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_id}/comments"
        r = self.safe_request("GET", url)
        if r is None:
            return []
        return r.json()

    def get_pull_request_files(self, owner: str, repo: str, pr_id: int) -> List[Dict]:
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_id}/files"
        r = self.safe_request("GET", url)
        if r is None:
            return []
        return r.json()

    def get_original_repo(self, owner: str, repo: str) -> Optional[str]:
        """
        Вернёт 'owner/repo' оригинального репозитория, если текущий репозиторий — форк.
        Если не форк или нельзя определить — вернёт None.
        """
        url = f"{self.api_base}/repos/{owner}/{repo}"
        r = self.safe_request("GET", url)
        if not r:
            return None
        try:
            data = r.json()
        except Exception:
            return None

        parent = data.get("parent")
        if data.get("fork") and isinstance(parent, dict):
            # нормальный путь
            if parent.get("full_name"):
                return parent["full_name"]
            # запасной путь (если вдруг нет full_name в ответе)
            p_owner = (
                parent.get("namespace", {}).get("path")
                or parent.get("namespace", {}).get("name")
                or parent.get("owner", {}).get("login")
            )
            p_repo = parent.get("path") or parent.get("name")
            if p_owner and p_repo:
                return f"{p_owner}/{p_repo}"
        return None

    def get_single_pull_request(self, owner: str, repo: str, pr_number: int) -> Optional[Dict]:
        """
        Получает детальную информацию по PR, включая mergeable_state.
        """
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_number}"
        r = self.safe_request("GET", url)
        if r is None:
            return None
        return r.json()


# --------------------------------------------------------------------
# Git detection and small helpers
# --------------------------------------------------------------------
def detect_git_repo():
    """Попытаться получить owner, repo, branch из git remote origin."""
    try:
        url = subprocess.check_output(
            ['git', 'config', '--get', 'remote.origin.url'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()

        if url.endswith('.git'):
            url = url[:-4]

        owner = repo = None
        if "@" in url and ":" in url and "/" in url:          # SSH: git@host:owner/repo
            path = url.split(":", 1)[1]
            owner, repo = path.split("/", 1)
        else:                                                  # HTTPS: https://host/owner/repo
            owner, repo = url.rstrip("/").split("/")[-2:]

        return owner, repo, branch
    except Exception:
        return None, None, None



def translate_question(ch: str) -> str:
    translations = {
        "发生了什么问题？": "What happened?",
        "期望行为是什么？": "What was the expected behavior?",
        "如何复现该缺陷": "How to reproduce the issue?",
        "其他补充信息": "Other additional info",
        "版本或分支信息": "Version or branch info",
        "新需求提供了什么功能？": "What feature is provided?",
        "该需求带来的价值、应用场景？": "Value and application scenario of this feature?"
    }
    return translations.get(ch.strip(), ch)


def normalize_title(text: str) -> str:
    return " ".join((text or "").split()).strip().casefold()


def resolve_pr_coordinates(args) -> Optional[Dict[str, str]]:
    owner = repo = pr_id = None
    if args.url:
        match = re.match(r"https?://[^/]+/([^/]+)/([^/]+)/(?:pulls?|merge_requests)/(\d+)", args.url)
        if match:
            owner, repo, pr_id = match.groups()
        else:
            print("❌ Invalid pull request URL format.")
            return None
    elif getattr(args, "repo", None) and getattr(args, "pr_id", None):
        owner, repo = args.repo.split("/")
        pr_id = args.pr_id
    else:
        input_val = prompt("Enter pull request URL or owner/repo > ")
        if input_val.startswith("http"):
            match = re.match(r"https?://gitcode\.com/([^/]+)/([^/]+)/(?:pulls?|merge_requests)/(\d+)", input_val)
            if match:
                owner, repo, pr_id = match.groups()
            else:
                print("❌ Invalid pull request URL format.")
                return None
        elif "/" in input_val:
            owner, repo = input_val.split("/")
            pr_id = prompt("Enter pull request ID > ")
        else:
            print("❌ Invalid input. Expected a URL or owner/repo format.")
            return None
    return {"owner": owner, "repo": repo, "pr_id": pr_id}


def find_duplicate_issue(client: GiteeClient, owner: str, repo: str, title: str) -> Optional[Dict]:
    wanted = normalize_title(title)
    if not wanted:
        return None
    for issue in client.list_issues(owner, repo, state="open", max_results=100):
        if normalize_title(issue.get("title", "")) == wanted:
            return issue
    return None


def _head_repo_branch(head_spec: str) -> Dict[str, str]:
    repo_full, _, branch = (head_spec or "").partition(":")
    owner = repo_full.split("/", 1)[0] if "/" in repo_full else repo_full
    return {"repo_full": repo_full, "owner": owner, "branch": branch}


def is_duplicate_pull_request(pr: Dict, title: str, head: str, base: str) -> bool:
    if normalize_title(pr.get("title", "")) == normalize_title(title):
        return True

    wanted = _head_repo_branch(head)
    head_info = pr.get("head", {}) or {}
    base_info = pr.get("base", {}) or {}
    actual_branch = head_info.get("ref", "")
    actual_base = base_info.get("ref", "")
    if wanted["branch"] and actual_branch != wanted["branch"]:
        return False
    if base and actual_base != base:
        return False

    actual_repo_full = (head_info.get("repo", {}) or {}).get("full_name", "")
    actual_owner = (head_info.get("user", {}) or {}).get("login", "")
    actual_label = head_info.get("label", "")

    if actual_repo_full and actual_repo_full == wanted["repo_full"]:
        return True
    if actual_owner and actual_owner == wanted["owner"]:
        return True
    if actual_label and actual_label.endswith(f":{wanted['branch']}"):
        return True
    return False


def find_duplicate_pull_request(client: GiteeClient, owner: str, repo: str, title: str, head: str, base: str) -> Optional[Dict]:
    for pr in client.list_pull_requests(owner, repo, state="open", max_results=100):
        if is_duplicate_pull_request(pr, title, head, base):
            return pr
    return None


def print_duplicate_match(kind: str, item: Dict):
    number = item.get("number") or item.get("iid") or item.get("id") or "?"
    title = item.get("title") or "(no title)"
    url = item.get("html_url") or item.get("url") or ""
    print(f"⚠️ Existing open {kind} looks like a duplicate:")
    print(f"  #{number} {title}")
    if url:
        print(f"  {url}")
    print("❌ Creation aborted. Reuse the existing item or pass --allow-duplicate.")


def format_named_people(users: List[Dict], role_key: str = "", accept_key: str = "accept") -> str:
    if role_key:
        users = [u for u in users if u.get(role_key, False)]
    if not users:
        return "-"
    rendered = []
    for user in sorted(users, key=lambda u: u.get("login", "")):
        login = user.get("login", "unknown")
        if accept_key in user:
            login = f"{login} ({'accepted' if user.get(accept_key) else 'pending'})"
        rendered.append(login)
    return ", ".join(rendered)


def print_pull_request_details(pr: Dict, owner: str, repo: str, files: List[Dict]):
    body = strip_html_tags(pr.get("body", "")) or "[empty]"
    assignees = pr.get("assignees", []) or []
    code_owners = [u for u in assignees if u.get("code_owner")]
    reviewers = [u for u in assignees if u.get("assignee")]
    testers = pr.get("testers", []) or []
    labels = [lbl.get("name") for lbl in pr.get("labels", []) if lbl.get("name")]
    head_ref = (pr.get("head", {}) or {}).get("ref", "-")
    base_ref = (pr.get("base", {}) or {}).get("ref", "-")
    head_repo = ((pr.get("head", {}) or {}).get("repo", {}) or {}).get("full_name", "")
    if head_repo:
        head_ref = f"{head_repo}:{head_ref}"
    print(f"PR #{pr.get('number', '?')} in {owner}/{repo}")
    print(f"Title: {pr.get('title', '-')}")
    print(f"State: {pr.get('state', '-')}")
    print(f"URL: {pr.get('html_url', '-')}")
    print(f"Author: {(pr.get('user', {}) or {}).get('login', '-')}")
    print(f"Created: {pr.get('created_at', '-')}")
    print(f"Updated: {pr.get('updated_at', '-')}")
    print(f"Base: {base_ref}")
    print(f"Head: {head_ref}")
    print(f"Reviewers: {format_named_people(reviewers, accept_key='accept')}")
    print(f"Code Owners: {format_named_people(code_owners, accept_key='accept')}")
    print(f"Testers: {format_named_people(testers, accept_key='accept')}")
    print(f"Labels: {', '.join(labels) if labels else '-'}")
    print()
    print("Description:")
    print(body)
    print()
    print(f"Changed Files ({len(files)}):")
    if not files:
        print("- [not available]")
        return
    for item in files:
        filename = item.get("filename") or item.get("new_path") or item.get("path") or item.get("old_path") or "[unknown]"
        status = item.get("status") or item.get("change_type") or ""
        suffix = f" [{status}]" if status else ""
        print(f"- {filename}{suffix}")


# --------------------------------------------------------------------
# Prepare data functions (unified, чтобы не дублировать логику)
# --------------------------------------------------------------------
def prepare_issue_data(args, client: GiteeClient) -> Optional[Dict]:
    """Собирает title/body/labels для issue и возвращает словарь с данными."""
    # --- resolve target repo for ISSUE ---
    if args.repo:
        try:
            owner, repo = args.repo.split("/")
        except ValueError:
            print("❌ Invalid --repo, expected owner/repo")
            return None
    else:
        # пробуем определить из текущего git-репозитория и взять оригинальный (parent) если это форк
        src_owner, src_repo, _ = detect_git_repo()
        if not src_owner or not src_repo:
            repo_input = prompt("Repository (owner/repo) where to create the issue > ").strip()
            try:
                owner, repo = repo_input.split("/")
            except ValueError:
                print("❌ Invalid repository format. Expected owner/repo.")
                return None
        else:
            original = client.get_original_repo(src_owner, src_repo)
            if original:
                print(f"ℹ️ Используется оригинальный репозиторий для Issue: {original} (обнаружен из {src_owner}/{src_repo})")
                owner, repo = original.split("/")
            else:
                print(f"ℹ️ Репозиторий назначения не указан. Использую текущий: {src_owner}/{src_repo}")
                owner, repo = src_owner, src_repo

    # проверка, что репозиторий доступен
    if not client.validate_repository(owner, repo):
        print(f"❌ Repository {owner}/{repo} not found or inaccessible.")
        return None

    # title
    title = args.title or prompt("Issue Title > ")

    # body: priority - desc_file -> repo template (for openharmony use central .gitcode) -> interactive
    if args.desc_file:
        with open(args.desc_file, "r", encoding="utf-8") as f:
            body = f.read()
    else:
        # try repo template first
        template = client.get_file_from_repo(owner, repo, ".gitcode/ISSUE_TEMPLATE.zh-CN.md")
        if template:
            body = choose_issue_description_ui(template, prompt_text="Issue Description > ")
        else:

            body = prompt("Issue Description > ")

    # labels: choose from repository labels
    #existing_labels = client.get_labels(owner, repo)
    wanted = "bug" if args.type == "bug" else "enhancement"
    #found_label = next((lbl for lbl in existing_labels if lbl.lower() == wanted), None)
    #labels = [found_label] if found_label else []
    labels = [wanted]

    return {
        "owner": owner,
        "repo": repo,
        "title": title,
        "body": body,
        "labels": labels
    }


def prepare_pr_data(args, client: GiteeClient, issue_url: Optional[str] = None) -> Optional[Dict]:
    """Собирает все параметры для создания PR без выполнения сетевого запроса."""
    # detect git repo
    src_owner, src_repo, src_branch = detect_git_repo()
    if not src_owner or not src_repo:
        repo_input = prompt("Repository (owner/repo) where your branch is located > ")
        src_owner, src_repo = repo_input.split("/")
    if not src_branch:
        src_branch = prompt("Current branch (source) > ")

    # target repo: if not provided, try to use original (parent) repo of the current fork
    if args.repo:
        tgt_repo_full = args.repo
    else:
        original = client.get_original_repo(src_owner, src_repo)
        if original:
            print(f"ℹ️ Используется оригинальный репозиторий для PR: {original} (обнаружен из {src_owner}/{src_repo})")
            tgt_repo_full = original
        else:
            print(f"ℹ️ Репозиторий назначения не указан. Использую текущий: {src_owner}/{src_repo}")
            tgt_repo_full = f"{src_owner}/{src_repo}"

    base = args.base or prompt("Target base branch (e.g. master) > ")
    #base = args.base

    if "/" not in tgt_repo_full:
        print("❌ Invalid target repo format, expected owner/repo")
        return None
    tgt_owner, tgt_repo = tgt_repo_full.split("/")

    if not client.validate_repository(tgt_owner, tgt_repo):
        print(f"❌ Target repository {tgt_owner}/{tgt_repo} not found or inaccessible.")
        return None

    if not client.validate_branch_exists(tgt_owner, tgt_repo, base):
        print(f"❌ Target branch '{base}' does not exist in {tgt_owner}/{tgt_repo}.")
        return None

    # description: priority desc_file -> choose from template vs commit message -> interactive
    if args.desc_file:
        with open(args.desc_file, "r", encoding="utf-8") as f:
            pr_body = f.read()
    else:
        commit_msg = ""
        try:
            commit_msg = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], text=True).strip()
        except Exception:
            commit_msg = ""
        # get template for current repository or in {owner}/.gitcode
        template = client.get_file_from_repo(tgt_owner, tgt_repo, ".gitcode/PULL_REQUEST_TEMPLATE.md")

        pr_body = choose_description_ui(template=template, commit_msg=commit_msg, prompt_text="PR Description > ")

    # if issue_url provided — inject or update IssueNo: line
    if issue_url:
        lines = pr_body.splitlines()
        for idx, line in enumerate(lines):
            if line.strip().startswith("IssueNo:"):
                # if there is no http link, append it; if there is, ask to replace
                if "http" not in line:
                    lines[idx] = f"{line.strip()} ({issue_url})"
                else:
                    confirm = prompt("Existing IssueNo has a link. Replace with new one? (yes/no) > ")
                    if confirm.lower().startswith("y"):
                        # keep original prefix (IssueNo:...) and append link
                        base_prefix = line.split("http")[0].strip()
                        lines[idx] = f"{base_prefix} ({issue_url})"
                break
        else:
            lines.insert(0, f"IssueNo: {issue_url}")
        pr_body = "\n".join(lines)

    # title fallback: first non-empty line of pr_body or prompt
    title = prompt("PR Title > ")
#    for l in pr_body.splitlines():
#        if l.strip():
#            title = l.strip()
#            break
#    if not title:
#        title = prompt("PR Title > ")

    head = f"{src_owner}/{src_repo}:{src_branch}"  # Gitcode expects "fork_owner:branch" or just "branch" if same repo/fork
    return {
        "tgt_owner": tgt_owner,
        "tgt_repo": tgt_repo,
        "title": title,
        "body": pr_body,
        "head": head,
        "base": base
    }


# --------------------------------------------------------------------
# Small UI helpers
# --------------------------------------------------------------------
def choose_description_ui(template: Optional[str], commit_msg: Optional[str], prompt_text: str = "Enter description > ") -> str:
    """Удобное меню для выбора описания PR/Issue основываясь на шаблоне или commit."""
    if template:
        print("📄 Template available:")
        print("-" * 60)
        print(template)
        print("-" * 60)
        choices = ["1 - Use template"]
        if commit_msg:
            choices.append("2 - Use commit message")
        choices.append("3 - Manual input")
        print("\n".join(choices))
        choice = prompt("Select [1/2/3] > ").strip()
        if choice == "1":
            print("📝 Вы можете отредактировать текст шаблона. Нажмите Enter, чтобы сохранить без изменений.")
            return prompt("Edit template > ", default=template)
        if choice == "2" and commit_msg:
            print("📝 Вы можете отредактировать текст коммита. Нажмите Enter, чтобы сохранить без изменений.")
            return prompt("Edit commit message > ", default=commit_msg)
        return prompt(prompt_text)
    if commit_msg:
        # do not override commit_msg if commit exists; still allow user to edit
        print("ℹ️ Using last commit message as description. Leave blank to edit.")
        answer = prompt(f"{prompt_text} (leave blank to use commit) > ")
        return commit_msg if not answer.strip() else answer
    return prompt(prompt_text)


def choose_issue_description_ui(template: Optional[str], prompt_text: str = "Issue Description > ") -> str:
    """Выбор описания Issue: шаблон (с редактированием) или ручной ввод."""
    if template:
        print("📄 Issue template found.")
        print("-" * 60)
        print(template)
        print("-" * 60)
        print("Выберите вариант:")
        print("  1 - Использовать шаблон (можно отредактировать)")
        print("  2 - Ввести вручную (пустое поле)")
        choice = prompt("Select [1/2] > ").strip()
        if choice == "1":
            print("📝 Отредактируйте текст шаблона. Нажмите Enter, чтобы принять.")
            return prompt("Edit issue description > ", default=template)
        # Любой другой ввод — ручной ввод с пустым дефолтом
        return prompt(prompt_text)
    # Шаблона нет — обычный ручной ввод
    return prompt(prompt_text)


# --------------------------------------------------------------------
# Listing PRs (parallel)
# --------------------------------------------------------------------
def filter_pull_requests(prs: List[Dict], include_draft: bool, since_date: Optional[datetime]) -> List[Dict]:
    out = []
    for pr in prs:
        if not include_draft and pr.get("draft", False):
            continue
        created = dateparser.isoparse(pr["created_at"])
        if created.tzinfo is not None:  # убираем таймзону
            created = created.replace(tzinfo=None)
        if since_date and created < since_date:
            continue
        out.append(pr)
    return out


def print_pr_item(pr: Dict, owner: str, repo: str, extended: bool = False, members_list=None, owner_config=None):
    created = dateparser.isoparse(pr["created_at"])
    conflicted = "⚠️ conflicted" if pr.get("conflict_passed", True) is False else ""
    drafted = "⚠️ draft" if pr.get("draft") is True else ""
    tgt_branch = pr.get("base", {}).get("ref", "")
    print(f"- #{pr['number']} {pr['title']} [{pr['state']}] by {pr['user']['login']} on {created.date()} {conflicted} {drafted}")
    print(f"  {pr.get('html_url')}")
    if tgt_branch:
        print(f"  base: {tgt_branch}")

    if extended:
        # labels
        labels = [lbl.get("name") for lbl in pr.get("labels", []) if "name" in lbl]
        if labels:
            colored_labels = [colorize_label(l) for l in labels]
            print(f"  labels: {', '.join(colored_labels)}")

        # reviewers
        reviewers = pr.get("assignees", [])
        if reviewers:
            sorted_reviewers = sort_and_colorize_users(reviewers, accept_key="accept", filter_key="assignee")
            print(f"  Reviewers: {', '.join(sorted_reviewers)}")

        # code owners
        code_owners = pr.get("assignees", [])
        if code_owners:
            sorted_codeowners = sort_and_colorize_users(code_owners, accept_key="accept", filter_key="code_owner")
            print(f"  Code Owners: {', '.join(sorted_codeowners)}")

        # testers
        testers = pr.get("testers", [])
        if testers:
            sorted_testers = sort_and_colorize_users(testers, accept_key="accept")
            print(f"  Testers: {', '.join(sorted_testers)}")

        # branch
        src_branch = pr.get("head", {}).get("ref", "")
        if src_branch or tgt_branch:
            print(f"  branch: {src_branch} -> {tgt_branch}")

        # --- Code Owners affected ---
        if owner_config and members_list:
            groups_dict = owner_config.get("groups", {}) or {}
            # groups_dict имеет ключи в виде URL, приводим к коротким именам
            short_groups = {
                url.split("/")[-1]: members for url, members in groups_dict.items()
            }
            members_set = set(members_list)
            affected: List[str] = []

            # берём всех логинов, которые реально code_owner = true в PR
            pr_code_owners = {
                a.get("login")
                for a in pr.get("assignees", [])
                if a.get("code_owner") is True
            }

            # проверяем только группы, которые реально у PR
            for g in pr_code_owners:
                if g not in short_groups:
                    continue
                # логины из owner_config.json для группы g
                group_users = [u.split("/")[-1] for u in short_groups[g] if isinstance(u, str)]
                # пересечение: code_owner логины ∩ members.txt ∩ группа
                affected.extend([u for u in group_users if u in members_set])

            if affected:
                print(f"  Code Owners affected: {', '.join(sorted(set(affected)))}")


def colorize_label(lbl: str) -> str:
    if lbl in SUCCESS_LABELS:
        return colorize(lbl, GREEN)
    if lbl in FAIL_LABELS:
        return colorize(lbl, RED)
    return lbl


def sort_and_colorize_users(users: List[Dict], accept_key: str = "accept", filter_key: str = "") -> List[str]:
    """
    Сортирует пользователей: сначала те, у кого accept=True.
    Красим зелёным если accept, красным если нет.
    Если указан filter_key, то учитываются только пользователи у которых он True.
    """
    # 1) отфильтруем по роли (если указана)
    filtered = [u for u in users if not filter_key or u.get(filter_key, False)]
    # 2) отсортируем: сначала те, у кого accept=True
    filtered.sort(key=lambda u: (not u.get(accept_key, False), u.get("login", "")))
    # 3) раскрасим
    out = []
    for u in filtered:
        login = u.get("login", "unknown")
        if u.get(accept_key, True):
            out.append(colorize(login+"(X)", GREEN))
        else:
            out.append(colorize(login, RED))
    return out


def handle_list_pr(args, client: GiteeClient):
    """
    Универсальная команда list-pr:
      - если нет параметров, поведение как старое list-pr-members: читаем members.txt (если есть)
      - можно указать --file (members file), --user (single user), --repos (comma separated)
      - параметры: --state, --include-draft, --since (YYYY-MM-DD), --group-by-user
    Выполняем параллельно запросы по репозиториям/авторам.
    """
    # authors resolution
    authors: List[str] = []
    use_default_listing = not args.file and not args.user and not args.repos
    if args.file:
        file_path = args.file if os.path.exists(args.file) else os.path.join(os.path.dirname(__file__), "members.txt")
        if not os.path.isfile(file_path):
            print(f"❌ Members file not found: {file_path}")
            return
        with open(file_path, "r", encoding="utf-8") as f:
            authors = [line.strip() for line in f if line.strip()]
    elif args.user:
        authors = [args.user]
    else:
        file_path = client.members
        if os.path.isfile(file_path):
            print("ℹ️ No user specified. Using members.txt by default.")
            with open(file_path, "r", encoding="utf-8") as f:
                authors = [line.strip() for line in f if line.strip()]
        elif use_default_listing:
            print("ℹ️ No user or members file specified. Showing PRs for all authors.")
        else:
            print("ℹ️ No members file found. Trying to get username from git.")
            try:
                default_user = subprocess.check_output(["git", "config", "user.name"], text=True).strip()
            except Exception:
                default_user = ""
            user_input = prompt(f"Enter user login (current: {default_user}) > ").strip()
            authors = [user_input or default_user]

    # repos resolution
    repos = args.repos.split(",") if args.repos else []
    if not repos:
        if use_default_listing:
            print(f"ℹ️ Repository not specified. Using default: {DEFAULT_LIST_PR_REPO}")
            repos = [DEFAULT_LIST_PR_REPO]
        else:
            repo_info = detect_git_repo()
            if repo_info and repo_info[0]:
                owner, repo, _ = repo_info
                original = client.get_original_repo(owner, repo)
                if original:
                    print(f"ℹ️ Используется оригинальный репозиторий: {original} (обнаружен из {owner}/{repo})")
                    repos = [original]
                else:
                    print(f"ℹ️ Repository not specified. Using current repo: {owner}/{repo}")
                    repos = [f"{owner}/{repo}"]
            else:
                print(f"ℹ️ Repository not specified. Using default: {DEFAULT_LIST_PR_REPO}")
                repos = [DEFAULT_LIST_PR_REPO]
    else:
        # репозитории заданы явно пользователем — просто нормализуем список
        repos = [r.strip() for r in repos if r.strip()]

    # parameters
    # 🆕 по умолчанию открытые PR
    state = args.state or ("all" if args.all else "open")
    include_draft = bool(args.include_draft)
    limit = args.limit  # 0 = без ограничения
    since_date = None
    if args.since:
        try:
            since_date = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print("❌ Invalid date format. Use YYYY-MM-DD.")
            return

    # build tasks: for each (repo, author) fetch PRs in parallel
    repo_grouped_results: Dict[str, List[Dict]] = {}
    owner_configs: Dict[str, Dict] = {}
    with _Spinner("Fetching PRs…") as spinner:
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_meta = {}
            for repo_full in repos:
                owner, repo = repo_full.strip().split("/")
                owner_configs[repo_full.strip()] = get_owner_config(client, owner, repo)
                requested_authors = authors or [None]
                for author in requested_authors:
                    fut = executor.submit(client.list_pull_requests, owner, repo, state, author, 50, limit)
                    future_to_meta[fut] = (owner, repo, author)

            for fut in as_completed(future_to_meta):
                owner, repo, author = future_to_meta[fut]
                try:
                    prs = fut.result()
                except Exception as e:
                    print(f"❌ Error fetching PRs for {owner}/{repo} author={author}: {e}")
                    prs = []
                prs = filter_pull_requests(prs, include_draft, since_date)
                key = f"{owner}/{repo}"
                repo_grouped_results.setdefault(key, []).extend(prs)

        # Apply sort + limit before fetching details (so we only detail-fetch what we show)
        for repo_key in list(repo_grouped_results):
            prs = repo_grouped_results[repo_key]
            prs.sort(key=lambda p: p.get("created_at", ""), reverse=True)
            if limit > 0:
                prs = prs[:limit]
            repo_grouped_results[repo_key] = prs

        # Fetch conflict state for visible PRs in parallel
        detail_tasks = [
            (owner_r, repo_r, pr)
            for repo_full, prs in repo_grouped_results.items()
            for owner_r, repo_r in [repo_full.split("/")]
            for pr in prs
        ]
        if detail_tasks:
            spinner.update(f"Fetching conflict state for {len(detail_tasks)} PRs…")

            def _fetch_conflict(task):
                o, r, pr = task
                detailed = client.get_single_pull_request(o, r, pr["number"])
                if detailed and "mergeable_state" in detailed:
                    pr["conflict_passed"] = detailed["mergeable_state"].get("conflict_passed", False)

            with ThreadPoolExecutor(max_workers=8) as detail_executor:
                list(detail_executor.map(_fetch_conflict, detail_tasks))

    # printing behavior: group_by_user => print grouped by user else print combined list
    for repo_full in repos:
        repo_key = repo_full.strip()
        owner_config = owner_configs.get(repo_key, {})
        repo_prs = repo_grouped_results.get(repo_key, [])

        total_label = f" (показано {len(repo_prs)}" + (f" из ≥{limit}, --limit 0 для всех" if limit > 0 and len(repo_prs) == limit else "") + ")"
        print(f"\n📂 {repo_key}{total_label}")
        if args.group_by_user or (args.user and not args.file):
            # print per-author lists
            for author in authors:
                print(f"\n👤 Author: {author}")
                author_prs = [p for p in repo_prs if p.get("user", {}).get("login") == author]
                if not author_prs:
                    print("ℹ️ No PRs for this author.")
                    continue
                for pr in author_prs:
                    print_pr_item(pr, *repo_key.split("/"), extended=args.extended_info, members_list=authors, owner_config=owner_config)
        else:
            # print combined
            if not repo_prs:
                print("ℹ️ No PRs found.")
            else:
                for pr in repo_prs:
                    print_pr_item(pr, *repo_key.split("/"), extended=args.extended_info, members_list=authors, owner_config=owner_config)


# --------------------------------------------------------------------
# Comment PR
# --------------------------------------------------------------------
def handle_comment_pr(args, client: GiteeClient):
    owner = repo = pr_id = None

    if args.url:
        match = re.match(r"https?://[^/]+/([^/]+)/([^/]+)/(?:pulls?|merge_requests)/(\d+)", args.url)
        if match:
            owner, repo, pr_id = match.groups()
        else:
            print("❌ Invalid pull request URL format.")
            return
    elif args.repo and args.pr_id:
        owner, repo = args.repo.split("/")
        pr_id = args.pr_id
    else:
        input_val = prompt("Enter pull request URL or owner/repo > ")
        if input_val.startswith("http"):
            match = re.match(r"https?://gitcode\.com/([^/]+)/([^/]+)/(?:pulls?|merge_requests)/(\d+)", input_val)
            if match:
                owner, repo, pr_id = match.groups()
            else:
                print("❌ Invalid pull request URL format.")
                return
        elif "/" in input_val:
            owner, repo = input_val.split("/")
            pr_id = prompt("Enter pull request ID > ")
        else:
            print("❌ Invalid input. Expected a URL or owner/repo format.")
            return

    comment = args.comment or prompt("Comment > ")
    res = client.comment_pull_request(owner, repo, int(pr_id), comment)
    if res:
        print("✅ Comment added.")
    else:
        print("❌ Failed to add comment.")


# --------------------------------------------------------------------
# Create functions that call prepare_* then client
# --------------------------------------------------------------------
def handle_create_issue(args, client: GiteeClient):
    issue_data = prepare_issue_data(args, client)
    if not issue_data:
        return
    if not getattr(args, "allow_duplicate", False):
        duplicate = find_duplicate_issue(client, issue_data["owner"], issue_data["repo"], issue_data["title"])
        if duplicate:
            print_duplicate_match("issue", duplicate)
            return
    print("\n--- Preview ---")
    print(issue_data["title"])
    print("-" * 60)
    print(issue_data["body"][:1000])
    print("-" * 60)
    if getattr(args, 'yes', False) or prompt("Create issue? (yes/no) > ").lower().startswith("y"):
        res = client.create_issue(issue_data["owner"], issue_data["repo"], issue_data["title"], issue_data["body"], issue_data["labels"])
        if res:
            print("✅ Issue created:", res.get("html_url"))
        else:
            print("❌ Issue creation failed.")


def handle_create_pr(args, client: GiteeClient):
    pr_data = prepare_pr_data(args, client)
    if not pr_data:
        return
    if not getattr(args, "allow_duplicate", False):
        duplicate = find_duplicate_pull_request(
            client,
            pr_data["tgt_owner"],
            pr_data["tgt_repo"],
            pr_data["title"],
            pr_data["head"],
            pr_data["base"],
        )
        if duplicate:
            print_duplicate_match("pull request", duplicate)
            return
    print("\n--- Preview ---")
    print(pr_data["title"])
    print("-" * 60)
    print(pr_data["body"][:1000])
    print("-" * 60)
    if getattr(args, 'yes', False) or prompt("Create PR? (yes/no) > ").lower().startswith("y"):
        res = client.create_pull_request(pr_data["tgt_owner"], pr_data["tgt_repo"], pr_data["title"], pr_data["body"], pr_data["head"], pr_data["base"])
        if res:
            print("✅ PR created:", res.get("html_url"))
        else:
            print("❌ PR creation failed.")


def handle_create_issue_and_pr(args, client: GiteeClient):
    # Сначала подготовим данные для issue и pr (без вызова API)
    issue_data = prepare_issue_data(args, client)
    if not issue_data:
        return
    pr_data = prepare_pr_data(args, client)
    if not pr_data:
        return
    if not getattr(args, "allow_duplicate", False):
        duplicate_issue = find_duplicate_issue(client, issue_data["owner"], issue_data["repo"], issue_data["title"])
        if duplicate_issue:
            print_duplicate_match("issue", duplicate_issue)
            return
        duplicate_pr = find_duplicate_pull_request(
            client,
            pr_data["tgt_owner"],
            pr_data["tgt_repo"],
            pr_data["title"],
            pr_data["head"],
            pr_data["base"],
        )
        if duplicate_pr:
            print_duplicate_match("pull request", duplicate_pr)
            return

    # Покажем предпросмотр и спросим подтверждение
    print("\n--- Preview Issue ---")
    print(issue_data["title"])
    print("-" * 60)
    print(issue_data["body"][:1000])
    print("-" * 60)
    print("\n--- Preview PR ---")
    print(pr_data["title"])
    print("-" * 60)
    print(pr_data["body"][:1000])
    print("-" * 60)
    if not (getattr(args, 'yes', False) or prompt("Create issue and PR sequentially? (yes/no) > ").lower().startswith("y")):
        print("❌ Aborted by user.")
        return

    # Создаём issue (последовательно)
    issue_res = client.create_issue(issue_data["owner"], issue_data["repo"], issue_data["title"], issue_data["body"], issue_data["labels"])
    if not issue_res:
        print("❌ Issue creation failed. Aborting.")
        return
    issue_url = issue_res.get("html_url")
    print("✅ Issue created:", issue_url)

    if issue_url:
        lines = pr_data["body"].splitlines()
        updated = False
        for idx, line in enumerate(lines):
            if line.strip().startswith("IssueNo:"):
                # если ссылки нет — допишем; если была — перезапишем аккуратно
                prefix = line.split("http")[0].strip()
                lines[idx] = f"{prefix} ({issue_url})"
                updated = True
                break
        if not updated:
            lines.insert(0, f"IssueNo: {issue_url}")
        pr_data["body"] = "\n".join(lines)
    if not pr_data:
        print("❌ Failed to prepare PR data after issue creation.")
        return

    pr_res = client.create_pull_request(pr_data["tgt_owner"], pr_data["tgt_repo"], pr_data["title"], pr_data["body"], pr_data["head"], pr_data["base"])
    if pr_res:
        print("✅ PR created:", pr_res.get("html_url"))
    else:
        print("❌ PR creation failed.")


# --------------------------------------------------------------------
# Show comments
# --------------------------------------------------------------------
def handle_show_comments(args, client: GiteeClient):
    resolved = resolve_pr_coordinates(args)
    if not resolved:
        return
    owner = resolved["owner"]
    repo = resolved["repo"]
    pr_id = resolved["pr_id"]
    comments = client.get_pull_request_comments(owner, repo, int(pr_id))
    if not comments:
        print("ℹ️ No comments found.")
        return
    print(f"\n💬 Comments for PR #{pr_id} in {owner}/{repo}:\n")
    for c in comments:
        author = c.get("user", {}).get("login", "unknown")
        date = c.get("created_at", "N/A")
        body = c.get("body", "")
        plain = strip_html_tags(body)
        print(f"--- {author} @ {date} ---")
        print(plain or "[empty]")
        print()


def handle_show_pr(args, client: GiteeClient):
    resolved = resolve_pr_coordinates(args)
    if not resolved:
        return
    owner = resolved["owner"]
    repo = resolved["repo"]
    pr_id = resolved["pr_id"]
    pr = client.get_single_pull_request(owner, repo, int(pr_id))
    if not pr:
        print("❌ Pull request not found.")
        return
    files = client.get_pull_request_files(owner, repo, int(pr_id))
    print_pull_request_details(pr, owner, repo, files)


# --------------------------------------------------------------------
# CLI main
# --------------------------------------------------------------------
def main():
    description = """\
    Gitcode Utility Tool — набор инструментов для работы с Gitcode API.

    Подсказка:
      Для подробной помощи по конкретной команде используйте:
        gitcode_util.py <команда> --help

    Пример:
      gitcode_util.py create-issue-pr --help
    """
    arg_parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = arg_parser.add_subparsers(dest="command", help="Доступные команды")

    # ===== create-issue =====
    p_issue = subparsers.add_parser(
        "create-issue",
        help="Создать Issue в репозитории",
        description="""Создаёт новую задачу (Issue) в указанном репозитории.

Примеры:
  gitcode_util.py create-issue --repo target_owner/target_repo --type bug --title "Ошибка" --desc-file bug.txt
  gitcode_util.py create-issue --repo target_owner/target_repo --type feature

Если --desc-file не указан, будет предложено ввести описание вручную
или выбрать шаблон (если он есть в репозитории).""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_issue.add_argument("--repo", required=True, help="Репозиторий назначения (owner/repo)")
    p_issue.add_argument("--type", choices=["bug", "feature"], required=True, help="Тип задачи")
    p_issue.add_argument("--title", help="Заголовок Issue")
    p_issue.add_argument("--desc-file", help="Файл с описанием Issue")
    p_issue.add_argument("--yes", "-y", action="store_true", help="Не запрашивать подтверждение")
    p_issue.add_argument("--allow-duplicate", action="store_true", help="Разрешить создание даже при найденном открытом дубликате")

    # ===== create-pr =====
    p_pr = subparsers.add_parser(
        "create-pr",
        help="Создать Pull Request",
        description="""Создаёт Pull Request из текущей локальной ветки в указанную ветку репозитория.

Примеры:
  gitcode_util.py create-pr --repo owner/repo --base master
  gitcode_util.py create-pr --repo owner/repo --desc-file pr_desc.txt

Если описание не указано, можно выбрать шаблон PR или использовать последний коммит.""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_pr.add_argument("--repo", help="Репозиторий назначения (owner/repo)")
    p_pr.add_argument("--base", default="master", help="Целевая ветка (по умолчанию master)")
    p_pr.add_argument("--desc-file", help="Файл с описанием PR")
    p_pr.add_argument("--yes", "-y", action="store_true", help="Не запрашивать подтверждение")
    p_pr.add_argument("--allow-duplicate", action="store_true", help="Разрешить создание даже при найденном открытом дубликате")

    # ===== comment-pr =====
    p_cmt = subparsers.add_parser(
        "comment-pr",
        help="Добавить комментарий к Pull Request",
        description="""Добавляет комментарий в указанный PR.

Примеры:
  gitcode_util.py comment-pr --repo owner/repo --pr-id 123 --comment "Отличная работа!"
  gitcode_util.py comment-pr --url https://gitcode.com/owner/repo/pull/123 --comment "Нужно поправить тесты"
""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_cmt.add_argument("--repo", help="Репозиторий назначения (owner/repo)")
    p_cmt.add_argument("--pr-id", help="ID Pull Request")
    p_cmt.add_argument("--url", help="Полный URL PR")
    p_cmt.add_argument("--comment", help="Текст комментария")

    # ===== list-pr =====
    p_list = subparsers.add_parser(
        "list-pr",
        help="Показать список Pull Request'ов",
        description="""Выводит список Pull Request'ов с фильтрацией по пользователям, дате и статусу.

Примеры:
  gitcode_util.py list-pr --repos owner/repo --user dev1
  gitcode_util.py list-pr --repos owner/repo --file members.txt --since 2025-08-01
  gitcode_util.py list-pr --all --include-draft

По умолчанию выводятся все открытые PR из файла members.txt или из текущего пользователя.""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_list.add_argument("--repos", help="Список репозиториев (через запятую)")
    p_list.add_argument("--user", help="Логин пользователя")
    p_list.add_argument("--file", help="Файл со списком логинов")
    p_list.add_argument("--state", help="Состояние PR (open, closed, all)")
    p_list.add_argument("--all", action="store_true", help="Показать все PR вне зависимости от состояния")
    p_list.add_argument("--include-draft", action="store_true", help="Включать черновики (draft)")
    p_list.add_argument("--since", help="Выводить PR, созданные после даты YYYY-MM-DD")
    p_list.add_argument("--limit", type=int, default=30, metavar="N",
                        help="Показать не более N самых новых PR (по дате создания). 0 = без ограничения. По умолчанию: 30.")
    p_list.add_argument("--group-by-user", action="store_true", help="Группировать вывод по пользователям")
    p_list.add_argument("--extended-info", action="store_true", help="Показывать расширенную информацию (labels, reviewers, code owners, testers)")

    # ===== create-issue-pr =====
    p_both = subparsers.add_parser(
        "create-issue-pr",
        help="Создать Issue и PR одновременно",
        description="""Создаёт новую задачу (Issue) и сразу Pull Request, привязанный к ней.

Примеры:
  gitcode_util.py create-issue-pr --repo owner/repo --type bug --title "Ошибка" --desc-file bug.txt
  gitcode_util.py create-issue-pr --repo owner/repo --type feature

Если --desc-file не указан, будет предложено ввести описание или выбрать шаблон.""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_both.add_argument("--repo", help="Репозиторий назначения (owner/repo, openharmony/arkui_ace_engine)")
    p_both.add_argument("--type", choices=["bug", "feature"], required=True, help="Тип задачи")
    p_both.add_argument("--title", help="Заголовок")
    p_both.add_argument("--desc-file", help="Файл с описанием")
    p_both.add_argument("--base", default="master", help="Целевая ветка PR (по умолчанию master)")
    p_both.add_argument("--yes", "-y", action="store_true", help="Не запрашивать подтверждение")
    p_both.add_argument("--allow-duplicate", action="store_true", help="Разрешить создание даже при найденном открытом дубликате")

    # ===== show-pr =====
    p_show_pr = subparsers.add_parser(
        "show-pr",
        help="Показать подробную информацию по PR",
        description="""Выводит карточку одного Pull Request с описанием, участниками и списком изменённых файлов.

Примеры:
  gitcode_util.py show-pr --repo owner/repo --pr-id 123
  gitcode_util.py show-pr --url https://gitcode.com/owner/repo/pulls/123""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_show_pr.add_argument("--url", help="Полный URL PR")
    p_show_pr.add_argument("--repo", help="Репозиторий (owner/repo)")
    p_show_pr.add_argument("--pr-id", help="ID PR")

    # ===== show-comments =====
    p_show = subparsers.add_parser(
        "show-comments",
        help="Показать комментарии к PR",
        description="""Выводит список комментариев к указанному Pull Request.

Примеры:
  gitcode_util.py show-comments --repo owner/repo --pr-id 123
  gitcode_util.py show-comments --url https://gitcode.com/owner/repo/pulls/123""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_show.add_argument("--url", help="Полный URL PR")
    p_show.add_argument("--repo", help="Репозиторий (owner/repo)")
    p_show.add_argument("--pr-id", help="ID PR")

    # ==== запуск ====
    try:
        args = arg_parser.parse_args()
    except argparse.ArgumentError as error:
        arg_parser.print_help()
        arg_parser.error(str(error))
    base_url, token, members, config_path = load_config()
    client = GiteeClient(base_url, token, members, config_path)

    if args.command == "create-issue":
        handle_create_issue(args, client)
    elif args.command == "create-pr":
        handle_create_pr(args, client)
    elif args.command == "comment-pr":
        handle_comment_pr(args, client)
    elif args.command == "list-pr":
        handle_list_pr(args, client)
    elif args.command == "create-issue-pr":
        handle_create_issue_and_pr(args, client)
    elif args.command == "show-comments":
        handle_show_comments(args, client)
    elif args.command == "show-pr":
        handle_show_pr(args, client)
    else:
        arg_parser.print_help()
        return 1

if __name__ == "__main__":
    main()
