# gitee_util

Утилита для взаимодействия с Gitee API.

## Возможности
- Создание Issue (`create-issue`)
- Создание Pull Request (`create-pr`)
- Добавление комментария к PR (`comment-pr`)
- Просмотр PR'ов (`list-pr`)

## Установка зависимостей
```bash
pip install -r requirements.txt
```

## Примеры использования

### Создание issue
```bash
python gitee_util.py create-issue --repo owner/repo --type bug
```

### Создание pull request
```bash
python gitee_util.py create-pr --base master
```

### Добавление комментария к PR
```bash
python gitee_util.py comment-pr --repo owner/repo --pr-id 123 --comment "LGTM"
```

### Просмотр PR'ов пользователя
```bash
python gitee_util.py list-pr --repos owner/repo1,owner/repo2 --user myuser --state open
```

### 🚀 Создание Issue и PR одной командой
```bash
python gitee_util.py create-issue-pr --repo owner/repo --type bug --base master
```
- автоматически создаёт issue и PR
- ссылка на issue добавляется в тело PR в строку `IssueNo:`
- если в описании уже есть строка `IssueNo:` со ссылкой — будет предложено её заменить

## Конфигурация
Укажите ваш токен и URL Gitee в `config.ini`:
```ini
[gitee]
gitee-url = https://gitee.com
token = your_token_here
```

---

## Примечания

### Что такое `base branch`?
`base` — это целевая ветка, в которую вы хотите сделать merge (обычно `master`, `main` или `develop`).

### Пример PR:
```bash
python gitee_util.py create-pr --repo openharmony/mycomp --base master
```

### Описание через файл
Вы можете задать описание PR или Issue из markdown-файла:
```bash
python gitee_util.py create-issue --repo myname/repo --type feature --desc-file ./desc.md
```

---

## Поддерживаемые поля шаблонов
При создании issue используется `.gitee/ISSUE_TEMPLATE` репозитория. Поля запрашиваются на английском, но сохраняются в оригинальном формате шаблона.

---

## Примеры строки `IssueNo:` в PR
```
IssueNo:#ICJTUA:[Bug]: Something broken
```
При создании PR с помощью `create-issue-pr`, скрипт автоматически добавит ссылку:
```
IssueNo:#ICJTUA:[Bug]: Something broken (https://gitee.com/owner/repo/issues/123)
```
Если ссылка уже есть — будет задан вопрос о замене.

---

## 📄 Лицензия
GNU GPL 3.0
