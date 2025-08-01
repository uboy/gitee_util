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

## Конфигурация
Укажите ваш токен и URL Gitee в `config.ini`:
```ini
[gitee]
gitee-url = https://gitee.com
token = your_token_here
```