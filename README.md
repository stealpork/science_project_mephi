# Stepork Science

React / TypeScript / Vite и Python / FastAPI / PostgreSQL. Сервисная архитектура, Docker Compose, общий Nginx, RabbitMQ, Redis и S3-совместимое файловое хранилище.

Запуск и эксплуатация: `deploy/`. Проверки: `backend/tests/`, `frontend/src/**/*.test.*`, `frontend/tests/`.

```bash
sh deploy/scripts/deploy-dev.sh up
sh deploy/scripts/deploy-dev.sh demo
sh deploy/scripts/deploy-dev.sh stop
```

Dev: http://localhost:18080. Production: `sh deploy/scripts/deploy-prod.sh --help`.
Публичные API-контракты: `contracts/openapi/`. Локальная техническая документация: `docs/` (не включается в Git).

Команда `demo` добавляет только в локальный dev два заполненных профиля, администратора, три проекта и две работы с настоящими PDF. Данные и пароли демонстрационные: `deploy/scripts/demo.py`. Повторный запуск не создаёт копии проектов и файлов и не сбрасывает чужие пароли.

Live-, браузерные live- и нагрузочные тесты автоматически очищают отдельный `science-test`: все прикладные таблицы (кроме миграций), версии файлов S3, очереди, Redis и перехваченную почту. Исходные справочники создаются заново. Этот стенд одноразовый, не храните в нём ручные данные. Dev и production очистка не принимает. После принудительного завершения процесса: `sh deploy/scripts/test.sh clean`.
