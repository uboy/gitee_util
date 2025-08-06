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
from prompt_toolkit import prompt
from bs4 import BeautifulSoup
from pathlib import Path

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

class GiteeClient:
    def __init__(self, base_url, token):
        self.api_base = f"{base_url}/api/v5"
        self.session = requests.Session()
        self.session.params = {"access_token": token}
        self._cache = {}  # 🔒 Кэш для шаблонов и других ресурсов

    def safe_request(self, method, url, **kwargs):
        try:
            r = self.session.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            print(f"❌ Gitee API error {r.status_code}: {r.text}")
            return None

    def get_issue_templates(self, owner, repo):
        url = f"{self.api_base}/repos/{owner}/{repo}/contents/.gitee/ISSUE_TEMPLATE"
        r = self.session.get(url)
        if r.status_code != 200:
            return []
        return r.json()

    def get_template_content(self, owner, repo, path):
        url = f"{self.api_base}/repos/{owner}/{repo}/contents/{path}"
        r = self.session.get(url)
        if r.status_code != 200:
            return None
        content = base64.b64decode(r.json().get("content", "")).decode('utf-8')
        return content

    def get_labels(self, owner, repo):
        url = f"{self.api_base}/repos/{owner}/{repo}/labels"
        r = self.session.get(url)
        if r.status_code != 200:
            return []
        return [label['name'] for label in r.json()]

    def create_issue(self, owner, repo, title, body, labels=None):
        if labels is None:
            labels = []
        url = f"{self.api_base}/repos/{owner}/issues"
        data = {
            "title": title,
            "body": body,
            "labels": ', '.join(labels),
            "repo": repo
        }
        r = self.session.post(url, json=data)
        if not r.ok:
            print(f"❌ Error {r.status_code}: {r.text}")
            r.raise_for_status()
        return r.json()

    def create_pull_request(self, owner, repo, title, body, head, base):
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls"
        data = {
            "title": title,
            "body": body or "",
            "head": head,  # e.g. "owner:feature_branch"
            "base": base
        }
        print("📤 Sending pull request with data:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        r = self.session.post(url, json=data)
        if not r.ok:
            print(f"❌ Error {r.status_code}: {r.text}")
            r.raise_for_status()
        return r.json()

    def list_pull_requests(self, owner, repo, state='open', author=None):
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls?state={state}"
        if author:
            url += f"&author={author}"
        r = self.session.get(url)
        if not r.ok:
            print(f"❌ Error {r.status_code}: {r.text}")
            r.raise_for_status()
        return r.json()

    def comment_pull_request(self, owner, repo, pr_number, comment):
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        data = {"body": comment}
        r = self.session.post(url, json=data)
        if not r.ok:
            print(f"❌ Error {r.status_code}: {r.text}")
            r.raise_for_status()
        return r.json()

    def validate_repository(self, owner, repo):
        url = f"{self.api_base}/repos/{owner}/{repo}"
        r = self.session.get(url)
        return r.ok

    def validate_branch_exists(self, owner, repo, branch):
        url = f"{self.api_base}/repos/{owner}/{repo}/branches/{branch}"
        r = self.session.get(url)
        return r.ok

    def get_pull_request_comments(self, owner, repo, pr_id):
        url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_id}/comments"
        r = self.session.get(url)
        r.raise_for_status()
        return r.json()


    def get_file_from_repo(self, owner, repo, path, ref="master"):
        """Получает содержимое файла (или первого файла из каталога) по пути с кэшированием"""
        cache_key = (owner, repo, path, ref)
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.api_base}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        r = self.safe_request("GET", url)
        if r is None:
            return None

        result = r.json()

        # Если это список (т.е. каталог), взять первый файл
        if isinstance(result, list):
            for item in result:
                if item.get("type") == "file":
                    nested_path = item.get("path")
                    return self.get_file_from_repo(owner, repo, nested_path, ref)
            return None

        # Если это файл
        content = result.get("content")
        if content:
            decoded = base64.b64decode(content).decode("utf-8")
            self._cache[cache_key] = decoded
            return decoded

        return None


def detect_git_repo():
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

        # Пример url: https://gitee.com/mazurdenis/gitee_utils.git
        if url.endswith('.git'):
            url = url[:-4]
        parts = url.split('/')[-2:]
        owner, repo = parts[0], parts[1]

        return owner, repo, branch
    except Exception:
        return None, None, None


def interactive_issue_input(template):
    sections = []
    for line in template.splitlines():
        if line.startswith("###"):
            q_chinese = line.strip("# ")
            q_english = translate_question(q_chinese)
            answer = prompt(f"{q_english}\n> ")
            while not answer.strip():
                print("⚠️ This field cannot be empty.")
                answer = prompt(f"{q_english}\n> ")
            sections.append(f"### {q_chinese}\n{answer}\n")
    return "\n".join(sections)


def translate_question(ch):
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


def load_config():
    config = ConfigParser()
    config.read(CONFIG_FILE, encoding='utf-8')
    base_url = config.get('gitee', 'gitee-url')
    token = config.get('gitee', 'token')
    return base_url, token


def handle_create_issue(args, client):
    owner, repo = args.repo.split("/")

    if not client.validate_repository(owner, repo):
        print(f"❌ Repository {owner}/{repo} not found or inaccessible.")
        return

    templates = client.get_issue_templates(owner, repo)
    path = None
    for t in templates:
        if args.type in t['name']:
            path = t['path']
            break
    template = client.get_template_content(owner, repo, path) if path else ""

    if args.desc_file:
        with open(args.desc_file, "r", encoding="utf-8") as f:
            body = f.read()
    else:
        body = ""
        if owner == "openharmony":
            content = client.get_file_from_repo(owner, ".gitee", ".gitee/ISSUE_TEMPLATE.zh-CN.md")
            if content:
                print("📄 Issue шаблон найден. Вы можете использовать его как есть или отредактировать.")
                print("-" * 60)
                print(content)
                print("-" * 60)
                answer = prompt("Enter issue description (leave blank to use template) > ")
                body = answer if answer.strip() else content

        if not body:
            body = prompt("Issue Description > ")

    title = args.title or prompt("Issue Title > ")

    existing_labels = client.get_labels(owner, repo)
    wanted_label = "bug" if args.type == "bug" else "enhancement"
    label = next((lbl for lbl in existing_labels if lbl.lower() == wanted_label), None)
    labels = [label] if label else []

    result = client.create_issue(owner, repo, title, body, labels)
    print("✅ Issue created:", result['html_url'])
    return result['html_url']


def handle_create_pr(args, client, issue_url=None):
    src_owner, src_repo, src_branch = detect_git_repo()
    if not src_owner or not src_repo:
        repo_input = prompt("Repository (owner/repo) > ")
        src_owner, src_repo = repo_input.split("/")
    if not src_branch:
        src_branch = prompt("Current branch (source) > ")
    base = args.base or prompt("Target base branch (e.g. master) > ")

    if not args.repo:
        tgt_repo_input = prompt("Target repository (owner/repo) > ")
    else:
        tgt_repo_input = args.repo
    tgt_owner, tgt_repo = tgt_repo_input.split("/")

    if not client.validate_repository(tgt_owner, tgt_repo):
        print(f"❌ Target repository {tgt_owner}/{tgt_repo} not found or inaccessible.")
        return

    if not client.validate_branch_exists(tgt_owner, tgt_repo, base):
        print(f"❌ Target branch '{base}' does not exist.")
        return

    if args.desc_file:
        with open(args.desc_file, 'r', encoding='utf-8') as f:
            pr_body = f.read()
    else:
        pr_body = ""
        commit_msg = ""
        try:
            commit_msg = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], text=True).strip()
        except Exception:
            pass

        template = None
        if tgt_owner == "openharmony":
            template = client.get_file_from_repo(tgt_owner, tgt_repo, ".gitee/PULL_REQUEST_TEMPLATE.zh-CN.md")

        if template:
            print("📄 PR шаблон найден. Вы можете выбрать одно из:")
            print("1 - Использовать шаблон")
            if commit_msg:
                print("2 - Использовать commit message")
            print("3 - Ввести вручную")
            print("-" * 60)
            choice = prompt("Выберите вариант [1/2/3] > ")

            if choice == "1":
                pr_body = template
            elif choice == "2" and commit_msg:
                pr_body = commit_msg
            else:
                pr_body = prompt("Введите описание PR > ")

        elif commit_msg:
            print("ℹ️ Используется описание последнего коммита")
            pr_body = commit_msg

        if not pr_body:
            pr_body = prompt("Введите описание PR > ")

    if issue_url:
        lines = pr_body.splitlines()
        for idx, line in enumerate(lines):
            if line.strip().startswith("IssueNo:"):
                if 'http' not in line:
                    lines[idx] = f"{line.strip()} ({issue_url})"
                else:
                    confirm = prompt("Replace existing IssueNo link with new one? (yes/no) > ")
                    if confirm.lower() == "yes":
                        lines[idx] = f"IssueNo: {issue_url}"
                break
        else:
            lines.insert(0, f"IssueNo: {issue_url}")
        pr_body = "\n".join(lines)

    title = prompt("PR Title > ")
    head = f"{src_owner}/{src_repo}:{src_branch}"

    print("Creating PR with the following info:")
    print(f"Source repo: {src_owner}/{src_repo}")
    print(f"From branch: {src_branch} (head = {head})")
    print(f"Target repo: {tgt_owner}/{tgt_repo}")
    print(f"To branch: {base}")
    print(f"Title: {title}")
    confirm = prompt("Proceed? (yes/no) > ")
    if confirm.lower() == "yes":
        result = client.create_pull_request(tgt_owner, tgt_repo, title, pr_body, head, base)
        print("✅ PR created:", result['html_url'])


def handle_comment_pr(args, client):
    owner = repo = pr_id = None

    if args.url:
        match = re.match(r"https://gitee.com/([^/]+)/([^/]+)/pulls/(\d+)", args.url)
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
            match = re.match(r"https://gitee.com/([^/]+)/([^/]+)/pulls/(\d+)", input_val)
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
    result = client.comment_pull_request(owner, repo, pr_id, comment)
    print("✅ Comment added:", result.get("html_url", ""))


def handle_list_pr(args, client):

    repos = args.repos.split(",") if args.repos else []
    if not repos:
        owner, repo, _ = detect_git_repo()
        if owner and repo:
            current_repo = f"{owner}/{repo}"
            user_input = prompt(f"Enter repositories to list PRs (comma-separated) [current: {current_repo}] > ")
            repos = [user_input] if user_input else [current_repo]
        else:
            user_input = prompt("Enter repositories to list PRs (comma-separated) > ")
            repos = user_input.split(",") if user_input else []

    try:
        default_user = subprocess.check_output(["git", "config", "user.name"], text=True).strip()
    except Exception:
        default_user = ""
    user_input = prompt(f"Enter user for listing PRs (current: {default_user}) > ")
    user = user_input or default_user

    #state = args.state or prompt("Enter PR state [open/closed/all] (default: open) > ") or "open"
    state = args.state or prompt_state()

    for repo_full in repos:
        owner, repo = repo_full.strip().split("/")
        print(f"\n📂 {repo_full.strip()}")
        prs = client.list_pull_requests(owner, repo, state, user)
        for pr in tqdm(prs, desc=f"{repo}", ncols=100):
            print(f"- #{pr['number']} {pr['title']} [{pr['state']}] {pr['html_url']}")


def handle_create_issue_and_pr(args, client):
    issue_url = handle_create_issue(args, client)
    if issue_url:
        handle_create_pr(args, client, issue_url)


def strip_html_tags(text):
    """Удаляет html-теги и приводит к читаемому виду"""
    text = html.unescape(text)
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(separator="\n")


def handle_show_comments(args, client):
    owner = repo = pr_id = None

    # Попробовать взять из аргументов
    if args.url:
        match = re.match(r"https://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", args.url)
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
            match = re.match(r"https://gitee\.com/([^/]+)/([^/]+)/pulls/(\d+)", input_val)
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

    try:
        comments = client.get_pull_request_comments(owner, repo, pr_id)
        if not comments:
            print("ℹ️ No comments found.")
            return
        print(f"\n💬 Comments for PR #{pr_id} in {owner}/{repo}:\n")
        for c in comments:
            author = c.get("user", {}).get("login", "unknown")
            date = c.get("created_at", "N/A")
            body = c.get("body", "")
            plain = strip_html_tags(body).strip()
            print(f"--- {author} @ {date} ---")
            print(plain or "[empty]")
            print()
    except requests.exceptions.HTTPError as e:
        print(f"❌ Failed to fetch comments: {e}")


def handle_list_pr_members(args, client):
    # Чтение логинов из файла
    members = get_members_from_file(args.file)

    if not members:
        print("❌ No members found.")
        return

    # Использовать репозиторий по умолчанию
    repos = args.repos.split(",") if args.repos else ["openharmony/arkui_ace_engine"]
    if not args.repos:
        print("ℹ️ Репозиторий не указан. Используется по умолчанию: openharmony/arkui_ace_engine")

    for repo_full in repos:
        owner, repo = repo_full.split("/")
        print(f"\n📂 {repo_full}")

        for member in members:
            prs = client.list_pull_requests(owner, repo, state="open", author=member)
            for pr in prs:
                conflicted = "⚠️ conflicted" if pr.get("mergeable") is False else ""
                created = pr["created_at"].split("T")[0]
                print(f"- #{pr['number']} {pr['title']} by {pr['user']['login']} on {created} {conflicted}")


def get_members_from_file(file_path=None):
    """Получить логины из файла, по умолчанию members.txt в текущем каталоге"""
    if not file_path:
        script_dir = Path(__file__).resolve().parent
        file_path = os.path.join(script_dir, "members.txt")

    if not os.path.isfile(file_path):
        print(f"❌ Members file not found: {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def prompt_state(default="open"):
    state_completer = WordCompleter(['open', 'closed', 'all'], ignore_case=True)
    state = prompt(f"Enter PR state (default: {default}) > ", completer=state_completer).strip()
    return state if state else default


def prompt_issue_type(default="bug"):
    type_completer = WordCompleter(['bug', 'feature', 'enhancement'], ignore_case=True)
    val = prompt(f"Issue type (default: {default}) > ", completer=type_completer).strip()
    return val if val else default


def main():
    parser = argparse.ArgumentParser(description="Gitee Utility Tool")
    subparsers = parser.add_subparsers(dest="command")

    p_issue = subparsers.add_parser("create-issue")
    p_issue.add_argument("--repo", required=True)
    p_issue.add_argument("--type", choices=["bug", "feature"], required=True)
    p_issue.add_argument("--title")
    p_issue.add_argument("--desc-file")

    p_pr = subparsers.add_parser("create-pr")
    p_pr.add_argument("--repo")
    p_pr.add_argument("--base")
    p_pr.add_argument("--desc-file")

    p_cmt = subparsers.add_parser("comment-pr")
    p_cmt.add_argument("--repo")
    p_cmt.add_argument("--pr-id")
    p_cmt.add_argument("--url")
    p_cmt.add_argument("--comment")

    p_list = subparsers.add_parser("list-pr")
    p_list.add_argument("--repos")
    p_list.add_argument("--user")
    p_list.add_argument("--state", default="open")

    p_both = subparsers.add_parser("create-issue-pr")
    p_both.add_argument("--repo", required=True)
    p_both.add_argument("--type", choices=["bug", "feature"], required=True)
    p_both.add_argument("--title")
    p_both.add_argument("--desc-file")
    p_both.add_argument("--base")

    p_show = subparsers.add_parser("show-comments")
    p_show.add_argument("--url")
    p_show.add_argument("--repo")
    p_show.add_argument("--pr-id")

    p_listm = subparsers.add_parser("list-pr-members")
    p_listm.add_argument("--repos")
    p_listm.add_argument("--file")

    args = parser.parse_args()
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
    elif args.command == "list-pr-members":
        handle_list_pr_members(args, client)

if __name__ == "__main__":
    main()
