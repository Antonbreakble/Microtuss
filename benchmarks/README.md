# Benchmarks

Два набора тестов на настоящих блоках. Они держат интерпретатор честным: если
изменить семантику и сломать реальный код, это заметно здесь, а не только в
`tests/`, где источники написаны под конкретную проверку.

```console
$ python3 benchmarks/drive/test_fb_motor.py          # 6 тестов
$ python3 benchmarks/drive/test_fb_drive.py          # 8 тестов
$ python3 benchmarks/analog_input/test_analog_input.py   # 32 теста
```

## `drive/` — учебный проект

`src/` содержит блок `FB_Motor` (упражнение почти на все конструкции языка),
проект `FB_Drive` с пользовательскими DUT и библиотекой `utils.st`, где `TON`
написан на ST и перекрывает встроенный.

* `test_fb_motor.py` — минимум: таймер, `VAR_IN_OUT`, вызов функции.
* `test_fb_drive.py` — DUT целиком словарём, массив из DUT, путь с индексом,
  библиотечный `TON`, поведение сброса.

## `analog_input/` — реальный блок из CODESYS

`src/` — экспорт проекта как есть, один объект на файл: блок `AnalogInput`,
вспомогательный блок `EmaFilter`, функции `LIMIT_R` / `SCALE_R` и DUT-типы.

```console
$ find benchmarks/analog_input/src -name '*.st' -exec python3 -m microtuss check {} +
ok: 4 POU(s), 42 type(s), 0 global(s)
  FUNCTION_BLOCK ANALOGINPUT
  FUNCTION_BLOCK EMAFILTER
  FUNCTION       LIMIT_R
  FUNCTION       SCALE_R
```

Что здесь проверяется: масштабирование 4..20 мА в 0..100 %, экспоненциальный
фильтр и его коэффициент, четыре аварийных порога с зоной нечувствительности,
биты диагностики в слове `Status`, имитация, команды сдвига. Блок исполняется
ровно в том виде, в каком поставляется на тест: без правок и без заглушек.

Подробности — в docstring самого `test_analog_input.py`.
