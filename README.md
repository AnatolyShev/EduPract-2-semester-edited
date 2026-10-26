# СРАВНЕНИЕ НА PCG BENCHMARK / ARCADE RULES
## Состав:
- replication_runs.zip: исходные результаты seed=2026, 2027, 2028, 2029 (без изменений).
- pcg_environment_manifest.json: версия 0.1.0, установленный Git commit, зависимости, SHA-256.
- verify_arcade_replications_v3.py: независимая проверка математических оценок/статусов/сертификатов без установки движка.
- replication_audit_v3.json: результат такой проверки.
- pcg_arcade_resource_experiment_v2.py и run_arcade_replications.py: использованный протокол повторов.
- collect_pcg_environment_v2.py: сбор манифеста программного окружения.

Проверить сохранённые результаты (Python 3.10+; сторонние пакеты не требуются):
- python verify_arcade_replications_v3.py --zip replication_runs.zip --manifest pcg_environment_manifest.json --output replication_audit_v3.json

Версия исходного PCG Benchmark:
- pcg_benchmark 0.1.0
- Git commit: 657aec8b3ad06b033bde4d88f02c3145bb7848fe
- https://github.com/amidos2006/pcg_benchmark/commit/657aec8b3ad06b033bde4d88f02c3145bb7848fe

Для получения нового окружения (не обязательно для аудита записанных данных):
- python -m pip install "git+https://github.com/amidos2006/pcg_benchmark.git@657aec8b3ad06b033bde4d88f02c3145bb7848fe"

## ОГРАНИЧЕНИЯ:
Сравнивается агентная процедура оценки правил из опубликованного PCG Benchmark,
а не генераторы Random Search / Evolution Strategy / Genetic Algorithm как целые системы.
Одинаков агентный бюджет, полный wall-clock время гибрида больше на 0,021786 с в этой выборке.
Повторяемость проверена на четырёх seed, независимой эталонной разметки правил нет.
