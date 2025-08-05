# gitee_util

Утилита для взаимодействия с Gitee API.

## Возможности
- Создание Issue (`create-issue`)
- Создание Pull Request (`create-pr`)
- Добавление комментария к PR (`comment-pr`)
- Просмотр PR'ов (`list-pr`)
- Просмотр комментариев к PR (`show-comments`)
- Создание Issue и PR одной командой (`create-issue-pr`)
- Вывод всех открытых PR для логинов из файла (`list-pr-members`)

## Установка зависимостей
```bash
pip install -r requirements.txt
```

## Конфигурация
Укажите ваш токен и URL Gitee в `config.ini` (в той же директории, что и скрипт):
```ini
[gitee]
gitee-url = https://gitee.com
token = your_token_here
```

## Примеры использования

### 🐛 Создание issue
```bash
python gitee_util.py create-issue --repo owner/repo --type bug --title "Crash on startup" --desc-file issue.md
```
- используется шаблон из `.gitee/ISSUE_TEMPLATE` репозитория
- если `--desc-file` не указан — текст запроса вводится вручную по шаблону
- если пользователь не ввёл описание — используется шаблон без изменений

### 📦 Создание pull request
```bash
python gitee_util.py create-pr --repo owner/repo --base master
```
- если `--desc-file` не указан, используется сообщение последнего коммита
- если выполняется из git-репозитория, текущая ветка и репозиторий определяются автоматически
- если пользователь не ввёл описание — используется шаблон `.gitee/PULL_REQUEST_TEMPLATE.zh-CN.md`

### 💬 Добавление комментария к PR
```bash
python gitee_util.py comment-pr --repo owner/repo --pr-id 123 --comment "LGTM"
```
или
```bash
python gitee_util.py comment-pr --url https://gitee.com/owner/repo/pulls/123
```

### 📋 Просмотр PR'ов пользователя
```bash
python gitee_util.py list-pr --repos owner/repo1,owner/repo2 --user myuser --state open
```
- если `--repos` не указано — используется текущий git-репозиторий (если доступен)
- если `--user` не указан — используется git user.name
- если `--state` не указан — будет предложено ввести (по умолчанию open)

### 📂 Просмотр открытых PR от участников
```bash
python gitee_util.py list-pr-members --repos owner/repo1 --file members.txt
```
- `members.txt` должен содержать список логинов (по одному в строке)
- для каждого PR выводится: номер, заголовок, автор, дата создания, статус `conflicted`

### 🗨️ Просмотр комментариев к PR
```bash
python gitee_util.py show-comments --url https://gitee.com/owner/repo/pulls/12345
```
или
```bash
python gitee_util.py show-comments --repo owner/repo --pr-id 12345
```
- если параметры не указаны, будет предложено ввести ссылку или owner/repo и номер PR
- форматированный вывод всех комментариев (автор, дата, текст)
- поддерживает такие же аргументы, как `list-pr`

### 🚀 Создание Issue и PR одной командой
```bash
python gitee_util.py create-issue-pr --repo owner/repo --type bug --base master --desc-file ./desc.md
```
- автоматически создаёт issue и PR
- ссылка на issue добавляется в тело PR в строку `IssueNo:`
- если в описании уже есть строка `IssueNo:` со ссылкой — будет предложено её заменить

---

## Что такое `base branch`?
`base` — это целевая ветка, в которую вы хотите сделать merge (обычно `master`, `main` или `develop`).

### Пример
```bash
python gitee_util.py create-pr --repo openharmony/mycomp --base master
```

### Описание через файл
```bash
python gitee_util.py create-issue --repo myname/repo --type feature --desc-file ./desc.md
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
