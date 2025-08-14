#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import requests
import subprocess
import base64
import json
from configparser import ConfigParser
from tqdm import tqdm
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
import re
import html
from bs4 import BeautifulSoup
from pathlib import Path
from dateutil import parser as dateparser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

# see https://gitee.com/api/v5/swagger
# --------------------------------------------------------------------
# Config / utils
# --------------------------------------------------------------------
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")


def load_config():
    config = ConfigParser()
    config.read(CONFIG_FILE, encoding='utf-8')
    base_url = config.get('gitee', 'gitee-url')
    token = config.get('gitee', 'token')
    return base_url.rstrip('/'), token


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
    def __init__(self, base_url: str, token: str):
        # base_url like "https://gitee.com"
        self.api_base = f"{base_url}/api/v5"
        self.session = requests.Session()
        # use access_token param to be compatible with Gitee API v5
        self.session.params = {"access_token": token}
        self._cache: Dict[Any, Any] = {}  # 🔒 Кэш для шаблонов и других ресурсов

    def safe_request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """Выполняет запрос и печатает понятную ошибку при неудаче."""
        try:
            r = self.session.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            text = getattr(e.response, "text", "")
            print(f"❌ Gitee API error: {status}\n{text}")
            return None
        except requests.RequestException as e:
            print(f"❌ Network error: {e}")
            return None


    # ---- templates/files ----
    def get_file_from_repo(self, owner: str, repo: str, path: str, ref: str = "master") -> Optional[str]:
        """
        Возвращает содержимое файла (или первого файла в каталоге) с кэшированием.
        path может быть файлом или директорией (.gitee/ISSUE_TEMPLATE).
        """
        cache_key = ("file", owner, repo, path, ref)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if owner == "openharmony":
            url = f"{self.api_base}/repos/{owner}/.gitee/contents/{path}?ref={ref}"
        else:
            url = f"{self.api_base}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        r = self.safe_request("GET", url)
        if r is None:
            return None

        result = r.json()

        # Если вернулся список (каталог) — попробуем найти первый файл
        if isinstance(result, list):
            for item in result:
                if item.get("type") == "file":
                    nested_path = item.get("path")
                    # рекурсивно получить содержимое первого файла
                    content = self.get_file_from_repo(owner, repo, nested_path, ref)
                    if content:
                        self._cache[cache_key] = content
                        return content
            return None

        # Если это объект с ключом content — декодируем
        content_b64 = result.get("content")
        if content_b64:
            decoded = base64.b64decode(content_b64).decode("utf-8")
            self._cache[cache_key] = decoded
            return decoded

        return None

    def get_issue_templates(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Вернуть список файлов в .gitee/ISSUE_TEMPLATE (если есть). Кэшируем."""
        cache_key = ("templates", owner, repo, "issue")
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.api_base}/repos/{owner}/{repo}/contents/.gitee/ISSUE_TEMPLATE"
        r = self.safe_request("GET", url)
        if r is None:
            return []
        if r.status_code != 200:
            return []
        res = r.json()
        self._cache[cache_key] = res
        return res

    def get_template_content(self, owner: str, repo: str, path: str) -> Optional[str]:
        """Получить содержание по path (wrapper для get_file_from_repo)."""
        if not path:
            return None
        return self.get_file_from_repo(owner, repo, path)

    def get_labels(self, owner: str, repo: str) -> List[str]:
        """Вернуть список меток репозитория (кэшируется)."""
        cache_key = ("labels", owner, repo)
        if cache_key in self._cache:
            return self._cache[cache_key]
        url = f"{self.api_base}/repos/{owner}/{repo}/labels"
        r = self.safe_request("GET", url)
        if r is None:
            return []
        try:
            labels = [lbl.get("name") for lbl in r.json() if "name" in lbl]
        except Exception:
            labels = []
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
            "labels": ', '.join(labels),
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

    def list_pull_requests(self, owner: str, repo: str, state: str = "open", author: Optional[str] = None, per_page: int = 50) -> List[Dict]:
        """
        Собирает PR'ы с пагинацией. Возвращает массив PR.
        """
        collected = []
        page = 1
        while True:
            url = f"{self.api_base}/repos/{owner}/{repo}/pulls?state={state}&per_page={per_page}&page={page}"
            if author:
                url += f"&author={author}"
            r = self.safe_request("GET", url)
            if r is None:
                break
            page_data = r.json()
            if not page_data:
                break
            collected.extend(page_data)
            # Если меньше, чем per_page — конец
            if len(page_data) < per_page:
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


# --------------------------------------------------------------------
# Git detection and small helpers
# --------------------------------------------------------------------
def detect_git_repo():
    """Попытаться получить owner, repo, branch из git remote origin."""
    try:
        url = subprocess.check_output(
            ['git', 'config', '--get', 'remote.origin.url'],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        # Normalize
        if url.endswith('.git'):
            url = url[:-4]
        parts = url.split('/')[-2:]
        owner, repo = parts[0], parts[1]
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


def interactive_issue_input(template: str) -> str:
    """Построчное заполнение шаблона (ищем заголовки '###' и запрашиваем ответы)."""
    if not template:
        return ""
    sections = []
    for line in template.splitlines():
        if line.startswith("###"):
            q_chinese = line.strip("# ").strip()
            q_english = translate_question(q_chinese)
            answer = prompt(f"{q_english}\n> ")
            while not answer.strip():
                print("⚠️ This field cannot be empty.")
                answer = prompt(f"{q_english}\n> ")
            sections.append(f"### {q_chinese}\n{answer}\n")
    return "\n".join(sections)


# --------------------------------------------------------------------
# Prepare data functions (unified, чтобы не дублировать логику)
# --------------------------------------------------------------------
def prepare_issue_data(args, client: GiteeClient) -> Optional[Dict]:
    """Собирает title/body/labels для issue и возвращает словарь с данными."""
    if not args.repo:
        print("❗ --repo is required for issue creation.")
        return None
    owner, repo = args.repo.split("/")
    if not client.validate_repository(owner, repo):
        print(f"❌ Repository {owner}/{repo} not found or inaccessible.")
        return None

    # title
    title = args.title or prompt("Issue Title > ")

    # body: priority - desc_file -> repo template (for openharmony use central .gitee) -> interactive
    if args.desc_file:
        with open(args.desc_file, "r", encoding="utf-8") as f:
            body = f.read()
    else:
        # try repo template first
        template = client.get_file_from_repo(owner, repo, ".gitee/ISSUE_TEMPLATE.zh-CN.md")
        if not template:
            # fallback to ISSUE_TEMPLATE directory
            templates = client.get_issue_templates(owner, repo)
            if templates:
                # pick first template file path
                tpath = templates[0].get("path")
                if tpath:
                    template = client.get_file_from_repo(owner, repo, tpath)
        if template:
            body = choose_issue_description_ui(template, prompt_text="Issue Description > ")
        else:
            body = prompt("Issue Description > ")

    # labels: choose from repository labels
    existing_labels = client.get_labels(owner, repo)
    wanted = "bug" if args.type == "bug" else "enhancement"
    found_label = next((lbl for lbl in existing_labels if lbl.lower() == wanted), None)
    labels = [found_label] if found_label else []

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

    base = args.base or prompt("Target base branch (e.g. master) > ")

    # target repo: either args.repo (owner/repo) or same as source repo
    tgt_repo_full = args.repo if args.repo else f"{src_owner}/{src_repo}"
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

        template = None
        # special-case for openharmony owner — use central PR template from that repo if available
        if tgt_owner == "openharmony":
            template = client.get_file_from_repo(tgt_owner, tgt_repo, ".gitee/PULL_REQUEST_TEMPLATE.zh-CN.md")

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

    head = f"{src_owner}/{src_repo}:{src_branch}"  # Gitee expects "fork_owner:branch" or just "branch" if same repo/fork
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


def print_pr_item(pr: Dict, owner: str, repo: str):
    created = dateparser.isoparse(pr["created_at"])
    conflicted = "⚠️ conflicted" if pr.get("mergeable") is False else ""
    print(f"- #{pr['number']} {pr['title']} [{pr['state']}] by {pr['user']['login']} on {created.date()} {conflicted}")
    print(f"  {pr.get('html_url')}")


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
        # default behavior: try members.txt, else git user
        file_path = os.path.join(os.path.dirname(__file__), "members.txt")
        if os.path.isfile(file_path):
            print("ℹ️ No user specified. Using members.txt by default.")
            with open(file_path, "r", encoding="utf-8") as f:
                authors = [line.strip() for line in f if line.strip()]
        else:
            try:
                default_user = subprocess.check_output(["git", "config", "user.name"], text=True).strip()
            except Exception:
                default_user = ""
            user_input = prompt(f"Enter user login (current: {default_user}) > ").strip()
            authors = [user_input or default_user]

    # repos resolution
    repos = args.repos.split(",") if args.repos else []
    if not repos:
        repo_info = detect_git_repo()
        if repo_info and repo_info[0]:
            owner, repo, _ = repo_info
            print(f"ℹ️ Repository not specified. Using current repo: {owner}/{repo}")
            repos = [f"{owner}/{repo}"]
        else:
            print("ℹ️ Repository not specified. Using default: openharmony/arkui_ace_engine")
            repos = ["openharmony/arkui_ace_engine"]

    # parameters
    # 🆕 по умолчанию открытые PR
    state = args.state or ("all" if args.all else "open")
    include_draft = bool(args.include_draft)
    since_date = None
    if args.since:
        try:
            since_date = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print("❌ Invalid date format. Use YYYY-MM-DD.")
            return

    # build tasks: for each (repo, author) fetch PRs in parallel
    tasks = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_meta = {}
        for repo_full in repos:
            owner, repo = repo_full.strip().split("/")
            for author in authors:
                fut = executor.submit(client.list_pull_requests, owner, repo, state, author)
                future_to_meta[fut] = (owner, repo, author)

        # collect and print
        repo_grouped_results: Dict[str, List[Dict]] = {}
        for fut in as_completed(future_to_meta):
            owner, repo, author = future_to_meta[fut]
            try:
                prs = fut.result()
            except Exception as e:
                print(f"❌ Error fetching PRs for {owner}/{repo} author={author}: {e}")
                prs = []
            # filter locally
            prs = filter_pull_requests(prs, include_draft, since_date)
            key = f"{owner}/{repo}"
            repo_grouped_results.setdefault(key, []).extend(prs)

        # printing behavior: group_by_user => print grouped by user else print combined list
        for repo_full in repos:
            repo_key = repo_full.strip()
            print(f"\n📂 {repo_key}")
            if args.group_by_user or (args.user and not args.file):
                # print per-author lists
                for author in authors:
                    print(f"\n👤 Author: {author}")
                    # find PRs in repo by that author
                    author_prs = [p for p in repo_grouped_results.get(repo_key, []) if p.get("user", {}).get("login") == author]
                    if not author_prs:
                        print("ℹ️ No PRs for this author.")
                        continue
                    for pr in author_prs:
                        print_pr_item(pr, *repo_key.split("/"))
            else:
                # print combined
                all_prs = repo_grouped_results.get(repo_key, [])
                if not all_prs:
                    print("ℹ️ No PRs found.")
                else:
                    for pr in all_prs:
                        print_pr_item(pr, *repo_key.split("/"))


# --------------------------------------------------------------------
# Comment PR
# --------------------------------------------------------------------
def handle_comment_pr(args, client: GiteeClient):
    owner = repo = pr_id = None

    if args.url:
        match = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", args.url)
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
            match = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", input_val)
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
    print("\n--- Preview ---")
    print(issue_data["title"])
    print("-" * 60)
    print(issue_data["body"][:1000])
    print("-" * 60)
    confirm = prompt("Create issue? (yes/no) > ")
    if confirm.lower().startswith("y"):
        res = client.create_issue(issue_data["owner"], issue_data["repo"], issue_data["title"], issue_data["body"], issue_data["labels"])
        if res:
            print("✅ Issue created:", res.get("html_url"))
        else:
            print("❌ Issue creation failed.")


def handle_create_pr(args, client: GiteeClient):
    pr_data = prepare_pr_data(args, client)
    if not pr_data:
        return
    print("\n--- Preview ---")
    print(pr_data["title"])
    print("-" * 60)
    print(pr_data["body"][:1000])
    print("-" * 60)
    confirm = prompt("Create PR? (yes/no) > ")
    if confirm.lower().startswith("y"):
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
    confirm = prompt("Create issue and PR sequentially? (yes/no) > ")
    if not confirm.lower().startswith("y"):
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
    owner = repo = pr_id = None
    if args.url:
        match = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", args.url)
        if match:
            owner, repo, pr_id = match.groups()
        else:
            print("❌ Invalid pull request URL format.")
            return
    elif args.repo and args.pr_id:
        owner, repo = args.repo.split("/")
        pr_id = args.pr_id
    else:
        # Интерактивный ввод
        input_val = prompt("Enter pull request URL or owner/repo > ")
        if input_val.startswith("http"):
            match = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", input_val)
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


# --------------------------------------------------------------------
# CLI main
# --------------------------------------------------------------------
def main():
    description = """\
    Gitee Utility Tool — набор инструментов для работы с Gitee API.

    Подсказка:
      Для подробной помощи по конкретной команде используйте:
        gitee_util.py <команда> --help

    Пример:
      gitee_util.py create-issue-pr --help
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
  gitee_util.py create-issue --repo owner/repo --type bug --title "Ошибка" --desc-file bug.txt
  gitee_util.py create-issue --repo owner/repo --type feature

Если --desc-file не указан, будет предложено ввести описание вручную
или выбрать шаблон (если он есть в репозитории).""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_issue.add_argument("--repo", required=True, help="Репозиторий в формате owner/repo")
    p_issue.add_argument("--type", choices=["bug", "feature"], required=True, help="Тип задачи")
    p_issue.add_argument("--title", help="Заголовок Issue")
    p_issue.add_argument("--desc-file", help="Файл с описанием Issue")

    # ===== create-pr =====
    p_pr = subparsers.add_parser(
        "create-pr",
        help="Создать Pull Request",
        description="""Создаёт Pull Request из текущей локальной ветки в указанную ветку репозитория.

Примеры:
  gitee_util.py create-pr --repo owner/repo --base master
  gitee_util.py create-pr --repo owner/repo --desc-file pr_desc.txt

Если описание не указано, можно выбрать шаблон PR или использовать последний коммит.""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_pr.add_argument("--repo", help="Репозиторий назначения (owner/repo)")
    p_pr.add_argument("--base", help="Целевая ветка (по умолчанию master)")
    p_pr.add_argument("--desc-file", help="Файл с описанием PR")

    # ===== comment-pr =====
    p_cmt = subparsers.add_parser(
        "comment-pr",
        help="Добавить комментарий к Pull Request",
        description="""Добавляет комментарий в указанный PR.

Примеры:
  gitee_util.py comment-pr --repo owner/repo --pr-id 123 --comment "Отличная работа!"
  gitee_util.py comment-pr --url https://gitee.com/owner/repo/pulls/123 --comment "Нужно поправить тесты"
""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_cmt.add_argument("--repo", help="Репозиторий (owner/repo)")
    p_cmt.add_argument("--pr-id", help="ID Pull Request")
    p_cmt.add_argument("--url", help="Полный URL PR")
    p_cmt.add_argument("--comment", help="Текст комментария")

    # ===== list-pr =====
    p_list = subparsers.add_parser(
        "list-pr",
        help="Показать список Pull Request'ов",
        description="""Выводит список Pull Request'ов с фильтрацией по пользователям, дате и статусу.

Примеры:
  gitee_util.py list-pr --repos owner/repo --user dev1
  gitee_util.py list-pr --repos owner/repo --file members.txt --since 2025-08-01
  gitee_util.py list-pr --all --include-draft

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
    p_list.add_argument("--group-by-user", action="store_true", help="Группировать вывод по пользователям")

    # ===== create-issue-pr =====
    p_both = subparsers.add_parser(
        "create-issue-pr",
        help="Создать Issue и PR одновременно",
        description="""Создаёт новую задачу (Issue) и сразу Pull Request, привязанный к ней.

Примеры:
  gitee_util.py create-issue-pr --repo owner/repo --type bug --title "Ошибка" --desc-file bug.txt
  gitee_util.py create-issue-pr --repo owner/repo --type feature

Если --desc-file не указан, будет предложено ввести описание или выбрать шаблон.""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_both.add_argument("--repo", required=True, help="Репозиторий (owner/repo)")
    p_both.add_argument("--type", choices=["bug", "feature"], required=True, help="Тип задачи")
    p_both.add_argument("--title", help="Заголовок")
    p_both.add_argument("--desc-file", help="Файл с описанием")
    p_both.add_argument("--base", help="Целевая ветка PR (по умолчанию master)")

    # ===== show-comments =====
    p_show = subparsers.add_parser(
        "show-comments",
        help="Показать комментарии к PR",
        description="""Выводит список комментариев к указанному Pull Request.

Примеры:
  gitee_util.py show-comments --repo owner/repo --pr-id 123
  gitee_util.py show-comments --url https://gitee.com/owner/repo/pulls/123""",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p_show.add_argument("--url", help="Полный URL PR")
    p_show.add_argument("--repo", help="Репозиторий (owner/repo)")
    p_show.add_argument("--pr-id", help="ID PR")

    # ==== запуск ====
    args = arg_parser.parse_args()
    base_url, token = load_config()
    client = GiteeClient(base_url, token)

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


if __name__ == "__main__":
    main()