# git_host_util

Утилита для взаимодействия с Gitee API и GitCode API.

Поддерживаются два провайдера:
- `gitee`
- `gitcode`

## Возможности
- Создание Issue (`create-issue`)
- Создание Pull Request (`create-pr`)
- Добавление комментария к PR (`comment-pr`)
- Просмотр PR'ов (`list-pr`)
- Просмотр одного PR с деталями (`show-pr`)
- Просмотр комментариев к PR (`show-comments`)
- Создание Issue и PR одной командой (`create-issue-pr`)
- Вывод всех открытых PR для логинов из файла (`list-pr-members`)

## Установка зависимостей
```bash
pip install -r requirements.txt
```

## Конфигурация
Конфиг хранится в `$XDG_CONFIG_HOME/gitee_util/config.ini`.
Если `XDG_CONFIG_HOME` не задан, используется `~/.config/gitee_util/config.ini`.
Если файла нет, утилита создаст его и попросит токен для выбранного провайдера.
```ini
[general]
provider = gitcode

[gitee]
gitee-url = https://gitee.com
token = your_token_here
members = members.txt

[gitcode]
gitcode-url = https://gitcode.com
token = your_token_here
members = members.txt
```

Относительный путь `members` резолвится относительно каталога конфига.

Приоритет выбора провайдера:
- `--provider` в командной строке (высший приоритет)
- `[general] provider` в `config.ini`
- значение по умолчанию: `gitcode`

## Примеры использования

### 🐛 Создание issue
```bash
python git_host_util.py --provider gitee create-issue --repo owner/repo --type bug --title "Crash on startup" --desc-file issue.md
```
- используется шаблон из `.gitee/ISSUE_TEMPLATE` репозитория
- если `--desc-file` не указан — текст запроса вводится вручную по шаблону
- если пользователь не ввёл описание — используется шаблон без изменений

### 📦 Создание pull request
```bash
python git_host_util.py --provider gitee create-pr --repo owner/repo --base master
```
- если `--desc-file` не указан, используется сообщение последнего коммита
- если выполняется из git-репозитория, текущая ветка и репозиторий определяются автоматически
- если пользователь не ввёл описание — используется шаблон `.gitee/PULL_REQUEST_TEMPLATE.zh-CN.md`
- перед созданием проверяется открытый дубликат по точному заголовку или по той же source/base ветке

### 💬 Добавление комментария к PR
```bash
python git_host_util.py --provider gitee comment-pr --repo owner/repo --pr-id 123 --comment "LGTM"
```
или
```bash
python git_host_util.py --provider gitee comment-pr --url https://gitee.com/owner/repo/pulls/123
```

### 📋 Просмотр PR'ов пользователя
```bash
python git_host_util.py --provider gitee list-pr --repos owner/repo1,owner/repo2 --user myuser --state open
```
- если `--repos` не указано — используется текущий git-репозиторий (если доступен)
- если `--user` не указан — используется git user.name
- если `--state` не указан — будет предложено ввести (по умолчанию open)

Быстрый сценарий по умолчанию:
```bash
python git_host_util.py list-pr
```
- по умолчанию используется провайдер `gitcode`
- если не заданы `--user`, `--file` и `--repos`, команда не запрашивает login
- в этом режиме показываются открытые PR для всех авторов в `openharmony/arkui_ace_engine`

### 📂 Просмотр открытых PR от участников
```bash
python git_host_util.py --provider gitee list-pr-members --repos owner/repo1 --file members.txt
```
- `members.txt` должен содержать список логинов (по одному в строке)
- для каждого PR выводится: номер, заголовок, автор, дата создания, статус `conflicted`

### 🗨️ Просмотр комментариев к PR
```bash
python git_host_util.py --provider gitee show-comments --url https://gitee.com/owner/repo/pulls/12345
```
или
```bash
python git_host_util.py --provider gitee show-comments --repo owner/repo --pr-id 12345
```
- если параметры не указаны, будет предложено ввести ссылку или owner/repo и номер PR
- форматированный вывод всех комментариев (автор, дата, текст)
- поддерживает такие же аргументы, как `list-pr`

### 🔎 Просмотр одного PR
```bash
python git_host_util.py --provider gitee show-pr --url https://gitee.com/owner/repo/pulls/12345
```
или
```bash
python git_host_util.py --provider gitcode show-pr --repo owner/repo --pr-id 12345
```
- выводит:
  - заголовок и описание
  - автора, статус, URL, base/head
  - reviewers, code owners, testers
  - изменённые файлы

### 🚀 Создание Issue и PR одной командой
```bash
python git_host_util.py --provider gitee create-issue-pr --repo owner/repo --type bug --base master --desc-file ./desc.md
```
- автоматически создаёт issue и PR
- ссылка на issue добавляется в тело PR в строку `IssueNo:`
- если в описании уже есть строка `IssueNo:` со ссылкой — будет предложено её заменить
- перед созданием ищет открытые дубликаты issue и PR и останавливается, если они уже существуют

---

## Что такое `base branch`?
`base` — это целевая ветка, в которую вы хотите сделать merge (обычно `master`, `main` или `develop`).

### Пример
```bash
python git_host_util.py --provider gitee create-pr --repo openharmony/mycomp --base master
```

### Описание через файл
```bash
python git_host_util.py --provider gitee create-issue --repo myname/repo --type feature --desc-file ./desc.md
```
- `--desc-file` может быть использован в любой из команд `create-*`

---

## Поддерживаемые поля шаблонов
- При создании issue используется `.gitee/ISSUE_TEMPLATE`
- При создании PR используется `.gitee/PULL_REQUEST_TEMPLATE.zh-CN.md`
- Поля запрашиваются на английском, но сохраняются в оригинальном виде (на китайском)

---

## Примеры строки `IssueNo:` в PR
```
IssueNo:#ICJTUA:[Bug]: Something broken
```
Автоматически обновляется при `create-issue-pr`:
```
IssueNo:#ICJTUA:[Bug]: Something broken (https://gitee.com/owner/repo/issues/123)
```
Если ссылка уже есть — будет задан вопрос о замене.

---

## 📄 Лицензия
GNU GPL 3.0
