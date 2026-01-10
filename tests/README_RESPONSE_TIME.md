# Тесты скорости отклика бота

## Описание

Комплексный набор тестов для измерения скорости отклика бота во всех режимах работы.

## Запуск тестов

### Все тесты производительности
```bash
pytest tests/test_response_time_comprehensive.py -v -s
```

### Конкретный тест
```bash
# Тест времени отклика callback.answer()
pytest tests/test_response_time_comprehensive.py::test_callback_answer_response_time -v -s

# Тест обработки UI callbacks
pytest tests/test_response_time_comprehensive.py::test_ui_callback_processing_time -v -s

# Тест рендеринга экранов
pytest tests/test_response_time_comprehensive.py::test_screen_rendering_time -v -s

# Тест запросов к БД (требует test_db_session)
pytest tests/test_response_time_comprehensive.py::test_database_query_time -v -s

# Тест Remna API
pytest tests/test_response_time_comprehensive.py::test_remna_api_response_time -v -s

# Тест синхронизации
pytest tests/test_response_time_comprehensive.py::test_sync_service_response_time -v -s

# Тест конкурентных запросов
pytest tests/test_response_time_comprehensive.py::test_concurrent_requests_performance -v -s

# Тест полного потока обработки
pytest tests/test_response_time_comprehensive.py::test_full_request_flow -v -s
```

### Комплексный тест (все компоненты)
```bash
pytest tests/test_response_time_comprehensive.py::test_comprehensive_response_time -v -s
```

## Интерпретация результатов

Тесты выводят детальную статистику:
- **Count**: Количество измерений
- **Min/Max**: Минимальное/максимальное время
- **Mean**: Среднее время
- **P50/P95/P99**: Перцентили (50% / 95% / 99% запросов быстрее)
- **Stdev**: Стандартное отклонение
- **Failures**: Количество ошибок

### Критические пороги

| Операция | Целевой P95 | Описание |
|----------|-------------|----------|
| `callback.answer()` | < 200ms | Мгновенный отклик на нажатие кнопки |
| UI callback обработка | < 300ms | Полная обработка UI callback |
| Рендеринг экрана | < 100ms | Генерация текста экрана |
| Создание клавиатуры | < 50ms | Построение клавиатуры |
| Запрос к БД | < 50ms | Одиночный запрос к базе данных |
| Remna API | < 1000ms | Запрос к внешнему API |
| Синхронизация | < 500ms | Полная синхронизация пользователя |

## Варианты оптимизации

Подробные рекомендации по оптимизации см. в:
- `docs/PERFORMANCE_OPTIMIZATION.md`

## Пример вывода

```
================================================================================
ОТЧЕТ О СКОРОСТИ ОТКЛИКА БОТА (режим: test)
================================================================================

Операция                                  Count    Min        Mean       P95        P99        Failures  
----------------------------------------------------------------------------------------------------
callback_answer                           100      0.50       1.20       2.50       3.00       0         
ui_callback_handler                       50       5.00       15.00      30.00      40.00      0         
screen_render                             50       2.00       5.00       10.00      15.00      0         

================================================================================
ПРОВЕРКА КРИТИЧЕСКИХ ПОРОГОВ:
----------------------------------------------------------------------------------------------------
✅ Все критические пороги соблюдены
```
