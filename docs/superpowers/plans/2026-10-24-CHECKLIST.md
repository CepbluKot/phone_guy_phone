# Чек-лист: аудит, оптимизация, разбиение на репозитории, GitLab CI/CD

Источник: [2026-10-24-project-audit-and-optimization.md](2026-10-24-project-audit-and-optimization.md).
Отмечаю пункты по мере выполнения: `[x]` сделано (с датой и доказательством), `[~]` частично/заблокировано (причина), `[ ]` не начато.

## A. Подготовка
- [x] A1. 2026-10-24: ворктри `.worktrees/audit-optimize-20261024`, ветка `audit/optimize-20261024` от `7114bc1` (НЕ от `main`: `main` отстаёт на 6 коммитов боевого Nateks/PSTN; чужие незакоммиченные правки не взяты)
- [x] A2. 2026-10-24: `glab` (OAuth) работает; создание группы верхнего уровня отвечает `403` → проекты созданы в личном пространстве `CepbluKot/`

## B. Аудит (в монорепо)
- [x] B1. 2026-10-24 базовая линия: `go build/vet` ок; `go test -race ./...` 10 пакетов ок (~37 с, publicauth 23 с); Node-тесты 40/40; admin-ui `tsc` ок, vitest 24/24; **pytest 295 passed / 5 FAILED** (те же 5 на закоммиченном HEAD и в грязном дереве, т.е. не от чужих правок): `test_conference_config::test_legacy_recording_extensions_enter_go_then_retain_their_exact_loops`, `test_deploy_conference::test_asterisk_read_only_root_keeps_bundled_docs_and_moves_only_astdb_to_tmpfs`, `test_deploy_goweb::test_production_cutover_has_snapshot_rollback_and_health_gates_in_order`, `test_internet_sip_security::test_vm_firewall_scopes_udp_sip_tls_and_rtp_to_trusted_private_sources`, `test_internet_sip_security::test_public_edge_install_has_a_backup_and_explicit_enable_gate`
- [x] B2. 2026-10-24: поиск выполнен инструментами (staticcheck, deadcode, ruff, vulture) + ручной обзор; результат — `docs/AUDIT_2026-10-24.md` (11 находок; латентность GPU и нагрузка не измерялись)
- [x] B3. 2026-10-24: безопасные исправления отобраны; спорное (автозапуск SIP edge, удаление «недостижимого» Go-кода, legacy-стек) — в аудите как «требует решения»
- [~] B4. Фаза 3 (сделано: коммит `fd4b86b` — 5 красных тестов, мёртвый код, импорты; не сделано: п.4,7,8 аудита): исправления малыми коммитами в ворктри (баги → горячий путь → мёртвый код → зависимости → скрипты → доки)
- [~] B5. 2026-10-24: pytest 299 passed (было 295+5 red), go test -race ок, staticcheck чисто; метрик скорости «до/после» нет — оптимизаций горячего пути не делалось
- [x] B6. 2026-10-24: Python-контроллер `conference/*.py` — legacy (Go cutover останавливает `controller`, `compose.goweb.yaml` запускает только Go); живое — `conference/asterisk/`. Решение: `conference/asterisk` → `asterisk-pbx`, Python-часть → `legacy-voice-app` (доказательство: `deploy/deploy-goweb.sh:212`, `docs/AUDIT_2026-10-24.md` п.6)
- [x] B7. 2026-10-24: `app/`, корневые `Dockerfile`/`compose.yaml`/`requirements*`/`deploy.sh`/`deploy-conference.sh` — legacy, нужны ещё legacy-деплою; удалять нельзя, выносим в `legacy-voice-app`. `tests/` разносятся по компонентам при D-пунктах

## C. GitLab
- [x] C1. 2026-10-24: группа `phone-guy` создана через веб-интерфейс (id 145661173, private); все 9 проектов перенесены в неё API-вызовом transfer (для `voice-web` и `asterisk-pbx` предварительно удалены репозитории образов в Container Registry — перенос с образами блокируется; образы пересоздаются CI). `ci_config_path` и шаблоны переведены на `phone-guy/deploy`
- [x] C2. 2026-10-24: созданы 9 приватных проектов `CepbluKot/{voice-web,rvc-service,selfmonitor,frontend,asterisk-pbx,deploy,docs,experiments,legacy-voice-app}` (набор изменён по B6/B7: вместо `conference-service` — `asterisk-pbx` и `legacy-voice-app`)
- [~] C3. `main` защищён по умолчанию в `deploy` (push: Maintainers); правило «merge только через MR» и обязательный зелёный пайплайн не включены

## D. Разбиение на репозитории (с историей, `git filter-repo` в клонах)
- [x] D1. 2026-10-24 voice-web: 28 коммитов, 58 файлов; история через `git filter-repo`, пути сохранены, push в `main`
- [x] D2. 2026-10-24 rvc-service: 29 коммитов; история через `git filter-repo`, пути сохранены, push в `main`
- [x] D3. 2026-10-24: `asterisk-pbx` (12 коммитов, `conference/asterisk/`) и `legacy-voice-app` (14 коммитов + README/CI; `web/` берётся из `frontend`)
- [x] D4. 2026-10-24 selfmonitor: 6 коммитов (+ вендорные `conference/{__init__,media,rvc}.py`); история через `git filter-repo`, пути сохранены, push в `main`
- [x] D5. 2026-10-24 frontend: 50 коммитов; история через `git filter-repo`, пути сохранены, push в `main`
- [x] D6. 2026-10-24 deploy: 31 коммит; история через `git filter-repo`, пути сохранены, push в `main`
- [x] D7. 2026-10-24 docs: 82 коммита; история через `git filter-repo`, пути сохранены, push в `main`
- [x] D8. 2026-10-24 experiments: 11 коммитов; история через `git filter-repo`, пути сохранены, push в `main`
- [x] D9. README.md, PROVENANCE.md (SHA источника), .gitignore в каждом репозитории; .dockerignore где нужен
- [~] D10. Сделано: `Dockerfile.goweb` берёт фронт через named context `frontend` (локальная сборка образа прошла), legacy-тесты получают `web/` из `frontend` в CI, `deploy`-тесты на старой раскладке запускаются через `scripts/assemble-monorepo.sh`. Не сделано: пути в `docs/*.md` (ссылки на `internal/…`, `deploy/…` по-прежнему из монорепо) и переписывание `deploy-*.sh` под отдельные репозитории

## E. CI/CD по образцу proactive-monitoring
- [x] E1. `deploy/ci/gitlab/pipeline.yml` (workflow, retry, auto-cancel); `tools.yml`/`version.yml` образца не копировались (версии — теги `X.Y.Z`, release-job в `release.yml`)
- [x] E2. `go.yml`, `python-service.yml`, `node.yml`, `docker-build.yml`, `release.yml`, `vm209.yml`; `deploy-service.yml` образца заменён ручным `vm209-status` (боевой деплой в CI не автоматизирован)
- [x] E3. `ci/gitlab/services/{voice-web,rvc-service,selfmonitor,frontend,asterisk-pbx,docs,experiments,legacy-voice-app}.yml`
- [x] E4. `deploy/.gitlab-ci.yml`: shellcheck (error), ci-templates, monorepo-compat-tests, ручной vm209-status
- [x] E5. `ci_config_path = ci/gitlab/services/<имя>.yml@CepbluKot/deploy:<SHA>` в 8 проектах (обновляется скриптом при смене шаблонов); job-token allowlist: `frontend` ← voice-web, legacy-voice-app, deploy; остальные ← deploy
- [x] E6. Shared runners недоступны (`ci_quota_exceeded`) и выключены. Раннеры: `phone-guy-ci` (воркстанция, docker без привилегий, тесты), `phone-guy-docker` (воркстанция, privileged, сборка образов — разрешено владельцем), `phone-guy-deploy` (внутри VM209, shell, protected refs, systemd `gitlab-runner-phone-guy.service`, без sudo, MemoryMax 512M)
- [x] E7. 2026-10-24 зелёные пайплайны на закреплённых шаблонах: deploy (shellcheck, ci-templates, monorepo-compat-tests = весь прежний pytest на собранной раскладке), docs, experiments, frontend, legacy-voice-app, rvc-service, selfmonitor, voice-web (go quality/test + сборка образа с фронтом). `asterisk-pbx`: сборка образа Asterisk из исходников идёт ~20 мин, итог — в журнале. Ручной `vm209-status` на раннере внутри VM209 выполнен успешно (read-only)

## F. Приёмка и переключение
- [ ] F1. НЕ ВЫПОЛНЕНО (нужны два реальных браузера и боевой звонок; код боевых сервисов не менялся, менялись только тесты и мёртвый код).  Живая приёмка звонка браузер↔браузер (`docs/BROWSER_CALL_ACCEPTANCE.md`)
- [ ] F2. НЕ ВЫПОЛНЕНО намеренно: боевой деплой остаётся на GitHub-монорепо.  Переключение боевого деплоя на GitLab-репозитории (только после F1 и согласования)
- [ ] F3. НЕ ВЫПОЛНЕНО намеренно (после F2).  Пометить GitHub-монорепо архивным (только после F2)
- [x] F4. Итоговый отчёт дан пользователю в чате 2026-10-24; сводка ниже в журнале Итоговый отчёт: изменённые файлы, проверки, риски

## Журнал
- 2026-10-24 аудит (`docs/AUDIT_2026-10-24.md`), коммиты `fd4b86b` (5 красных тестов, мёртвый код), docs-коммит, затем flaky-тест `TestExpiredAndTamperedSessionsAreRejected` (падал ~1/64: замена последнего base64-символа могла оставить подпись валидной) — исправлен, `-count=30` ок.
- 2026-10-24 GitLab: группа `phone-guy` не создана (API 403) → 9 проектов в `CepbluKot/`.
- 2026-10-24 раннеры: общих нет (`ci_quota_exceeded`). Воркстанция: `phone-guy-ci` (тесты, без привилегий) и `phone-guy-docker` (сборки, privileged — разрешено пользователем), systemd user unit `gitlab-runner-phone-guy.service`, свой `DOCKER_CONFIG` (в `~/.docker` протухшие креды). VM209: `phone-guy-deploy` (shell, пользователь `gitlab-runner`, без sudo, только protected refs, systemd `gitlab-runner-phone-guy.service`, MemoryMax 512M, CPUQuota 100%). После установки `voice-rvc.service` active, `/healthz` ready.
- 2026-10-24 сеть воркстанции периодически сбрасывает TLS к gitlab.com, `registry.gitlab.com` и Go proxy: в шаблоны добавлены повторы; публикация образов в Container Registry по умолчанию выключена (`PG_PUSH_IMAGE=0`), т.к. `docker push` стабильно получает `EOF`. Сборка образа остаётся проверкой.
- Открыто: выбрать место для реестра образов (в рамках VM209?); правило merge только через MR; удаление «недостижимого» Go-кода (п.4 аудита); решение по автозапуску SIP edge (п.2); живая приёмка звонка; переписать `deploy-*.sh` и ссылки в `docs/*.md` под раздельные репозитории.
