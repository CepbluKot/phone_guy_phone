# Чек-лист: аудит, оптимизация, разбиение на репозитории, GitLab CI/CD

Источник: [2026-10-24-project-audit-and-optimization.md](2026-10-24-project-audit-and-optimization.md).
Отмечаю пункты по мере выполнения: `[x]` сделано (с датой и доказательством), `[~]` частично/заблокировано (причина), `[ ]` не начато.

## A. Подготовка
- [x] A1. 2026-10-24: ворктри `.worktrees/audit-optimize-20261024`, ветка `audit/optimize-20261024` от `7114bc1` (НЕ от `main`: `main` отстаёт на 6 коммитов боевого Nateks/PSTN; чужие незакоммиченные правки не взяты)
- [~] A2. `glab` залогинен как `CepbluKot`, чтение чужих групп работает, API периодически отдаёт `EOF` (повторять запросы); права на создание группы ещё не проверены

## B. Аудит (в монорепо)
- [x] B1. 2026-10-24 базовая линия: `go build/vet` ок; `go test -race ./...` 10 пакетов ок (~37 с, publicauth 23 с); Node-тесты 40/40; admin-ui `tsc` ок, vitest 24/24; **pytest 295 passed / 5 FAILED** (те же 5 на закоммиченном HEAD и в грязном дереве, т.е. не от чужих правок): `test_conference_config::test_legacy_recording_extensions_enter_go_then_retain_their_exact_loops`, `test_deploy_conference::test_asterisk_read_only_root_keeps_bundled_docs_and_moves_only_astdb_to_tmpfs`, `test_deploy_goweb::test_production_cutover_has_snapshot_rollback_and_health_gates_in_order`, `test_internet_sip_security::test_vm_firewall_scopes_udp_sip_tls_and_rtp_to_trusted_private_sources`, `test_internet_sip_security::test_public_edge_install_has_a_backup_and_explicit_enable_gate`
- [x] B2. 2026-10-24: поиск выполнен инструментами (staticcheck, deadcode, ruff, vulture) + ручной обзор; результат — `docs/AUDIT_2026-10-24.md` (11 находок; латентность GPU и нагрузка не измерялись)
- [x] B3. 2026-10-24: безопасные исправления отобраны; спорное (автозапуск SIP edge, удаление «недостижимого» Go-кода, legacy-стек) — в аудите как «требует решения»
- [~] B4. Фаза 3 (сделано: коммит `fd4b86b` — 5 красных тестов, мёртвый код, импорты; не сделано: п.4,7,8 аудита): исправления малыми коммитами в ворктри (баги → горячий путь → мёртвый код → зависимости → скрипты → доки)
- [~] B5. 2026-10-24: pytest 299 passed (было 295+5 red), go test -race ок, staticcheck чисто; метрик скорости «до/после» нет — оптимизаций горячего пути не делалось
- [x] B6. 2026-10-24: Python-контроллер `conference/*.py` — legacy (Go cutover останавливает `controller`, `compose.goweb.yaml` запускает только Go); живое — `conference/asterisk/`. Решение: `conference/asterisk` → `asterisk-pbx`, Python-часть → `legacy-voice-app` (доказательство: `deploy/deploy-goweb.sh:212`, `docs/AUDIT_2026-10-24.md` п.6)
- [x] B7. 2026-10-24: `app/`, корневые `Dockerfile`/`compose.yaml`/`requirements*`/`deploy.sh`/`deploy-conference.sh` — legacy, нужны ещё legacy-деплою; удалять нельзя, выносим в `legacy-voice-app`. `tests/` разносятся по компонентам при D-пунктах

## C. GitLab
- [ ] C1. Создать группу (рабочее имя `phone-guy`, private)
- [ ] C2. Создать 9 проектов: `voice-web`, `rvc-service`, `selfmonitor`, `frontend`, `asterisk-pbx`, `deploy`, `docs`, `experiments`, `legacy-voice-app` (набор изменён по B6/B7: вместо `conference-service` — `asterisk-pbx` и `legacy-voice-app`)
- [ ] C3. Защитить `main`, merge только через MR

## D. Разбиение на репозитории (с историей, `git filter-repo` в клонах)
- [ ] D1. `voice-web` (cmd/, internal/, go.*, Dockerfile.goweb)
- [ ] D2. `rvc-service` (rvc_service/, Dockerfile, requirements*)
- [ ] D3. `asterisk-pbx` (conference/asterisk/) и `legacy-voice-app` (app/, conference/*.py, корневые Dockerfile/compose/requirements, deploy.sh, deploy-conference.sh)
- [ ] D4. `selfmonitor` (selfmonitor/)
- [ ] D5. `frontend` (web/, web-rt/, admin-ui/)
- [ ] D6. `deploy` (deploy/, infra/, compose*.yaml)
- [ ] D7. `docs` (docs/, README, START_HERE, AGENTS.md)
- [ ] D8. `experiments` (experiments/)
- [ ] D9. В каждый репозиторий: README.md, PROVENANCE.md, .gitignore, .dockerignore
- [ ] D10. Переписать межрепозиторные ссылки (Dockerfile.goweb ← frontend, deploy-скрипты, пути в доках)

## E. CI/CD по образцу proactive-monitoring
- [ ] E1. `deploy`: `ci/gitlab/pipeline.yml`, `tools.yml`, `version.yml`
- [ ] E2. `deploy`: `go.yml`, `python-service.yml`, `node.yml`, `docker-build.yml`, `deploy-service.yml`
- [ ] E3. `deploy`: `ci/gitlab/services/<имя>.yml` для каждого репозитория
- [ ] E4. `deploy`: собственный `.gitlab-ci.yml` (shellcheck, compose config, pytest шаблонов)
- [ ] E5. Выставить `ci_config_path` с закреплённым SHA в каждом проекте
- [ ] E6. Раннер: проверить shared runners, решить по своему раннеру
- [ ] E7. Все пайплайны зелёные

## F. Приёмка и переключение
- [ ] F1. Живая приёмка звонка браузер↔браузер (`docs/BROWSER_CALL_ACCEPTANCE.md`)
- [ ] F2. Переключение боевого деплоя на GitLab-репозитории (только после F1 и согласования)
- [ ] F3. Пометить GitHub-монорепо архивным (только после F2)
- [ ] F4. Итоговый отчёт: изменённые файлы, проверки, риски

## Журнал
(по мере работы)
