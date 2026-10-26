# iecst — тестирование Function Block из CODESYS на Python

Интерпретатор Structured Text (IEC 61131-3). Берёт исходник FB, поданный как
`.st`-файл (или папку файлов — по объекту на файл, как их отдаёт CODESYS),
прогоняет его по сканам и отдаёт значения переменных. Тесты пишутся обычным
Python: без CODESYS, без контроллера, без стенда.

```console
$ iecst run examples/valve.st --set Cmd=true --scans 3 --expect Busy=true
FB_VALVE: 1/1 expectations passed
```

## Быстрый старт

Нужен Python 3.11+ и ничего больше — зависимостей нет.

```console
$ pip install -e .
$ python3 examples/01_first_test.py
```

```python
from iecst import Testbench

tb = Testbench.from_file("valve.st", "FB_Valve", cycle_time="10ms")

tb.scan(Cmd=True)      # подать вход и выполнить один скан
tb.run(200)            # ещё 200 сканов = 2 с виртуального времени

tb.expect(Open=True, Busy=False)
print(tb.summary())
```

```console
FB_Valve: 2/2 expectations passed
```

`expect` не бросает исключение, а записывает результат — один запуск сообщает
сразу обо всех расхождениях, с обоими значениями и номером скана. Итог лежит
в `tb.failures`, а `tb.summary()` печатает их списком. Когда удобнее упасть
сразу — `assert` по `tb.get(...)` работает как обычно.

## Как это работает

```
   .st файлы
       │  lexer.py       токены: базы счисления, T#/D#/DT#, вложенные комментарии
       ▼
   дерево разбора
       │  parser.py      рекурсивный спуск, приоритеты операторов по стандарту
       ▼
   ast_nodes.py
       │  model.py       типы (DUT + встроенные), POU, глобалы, stdlib
       ▼
   слинкованная модель
       │  interpreter.py исполнение по сканам на виртуальном клоке
       ▼
   значения переменных  ──  api.py (Testbench) / cli.py
```

Ключевое решение: **время виртуальное**. `cycle_time` задаёт, сколько времени
означает один скан, и таймеры считают от этого, а не от `time.time()`. Тест с
`TON` на 2 секунды проходит одинаково на быстрой машине и на загруженной.

## Примеры

В `examples/` — восемь коротких скриптов, по одному на тему, от «подать вход и
проверить выход» до разбора ошибок в тестах. Запускаются как есть:

```console
$ python3 examples/02_timers.py             # виртуальное время и таймеры
$ python3 examples/05_a_project.py          # проект: DUT, библиотека, свой TON
$ python3 examples/06_test_a_project.py     # готовый набор тестов — образец
$ python3 examples/08_mistakes.py           # как выглядят ошибки
```

Список с пояснениями — в [`examples/README.md`](examples/README.md).

В `benchmarks/` — тесты на настоящих блоках: учебный проект с DUT и библиотекой
и реальный `AnalogInput` из CODESYS (32 теста на масштабирование, фильтр,
аварийные пороги, биты статуса). См. [`benchmarks/README.md`](benchmarks/README.md).

## Что умеет Testbench

| Метод | Назначение |
|---|---|
| `from_file(path, pou, includes=[...])` | загрузить файл и его библиотеки |
| `scan(**inputs)` | подать входы и выполнить один скан |
| `set(name, value)` / `set_many({...})` | записать переменную |
| `step()` / `run(n)` | выполнить 1 / n сканов |
| `get(name)` / `state()` | прочитать переменную / снимок всех |
| `expect(**vars)` | проверить несколько переменных, записав результат |
| `failures` / `summary()` | что не совпало / отчёт строкой |
| `runtime.call("F", ...)` | вызвать `FUNCTION` напрямую |

Пути к переменным пишутся как в ST: `travel.ET` достаёт вложенный FB,
`Configs[2].Mode` — элемент массива (с проверкой границ), `Status.3` — бит.

## Значения из теста

Привычные питоновские литералы: `True` для `BOOL`, `100` для `INT`, `1.5` для
`REAL`. Для типов, у которых нет питоновского написания, работает «полевая»
запись — и не только на верхнем уровне, но и внутри структур и массивов:

```python
tb.set(TravelTime="1m30s")            # TIME: "500ms", "T#5s", "1m30s"
tb.set(State="Open")                  # перечисление: имя члена, без регистра
tb.set(Limits={"lo": 0.0, "hi": 50.0})  # DUT целиком словарём
tb.set("Configs[2].Mode", "Automatic")
```

Опечатки не проходят молча — сообщение называет допустимые варианты:

```
'T_LIMITS' has no member(s) LOL (expected: HI, LO)
'FULLISH' is not a member of E_FILL (expected one of: IDLE, FILLING, FULL, OVERFLOW)
array index 3 out of range 1..2 for ARRAY[1..2] OF T_DRIVECONFIG
```

Обратно: `get()` возвращает число, а `state()` рендерит `TIME` как `T#2s`, а
перечисление — именем члена, потому что `'OPEN'` читается, а `1` нет.

## Работа с экспортом CODESYS

Проект поставляется так, как его отдаёт CODESYS, и править его не нужно:

* **Один объект — один файл.** Блок, DUT и функции лежат в разных файлах и
  линкуются вместе; порядок файлов не важен.
* **Закрывающее ключевое слово можно не писать.** Экспорт объекта обрывается
  на EOF, и это нормально принимается — но настоящая синтаксическая ошибка
  по-прежнему сообщается.
* **Прагмы `{attribute 'symbol' := ...}` игнорируются.** Это метаданные для
  OPC-слоя, к логике отношения не имеют.
* **Модификаторы `PUBLIC` / `FINAL` / `ABSTRACT`** перед именем блока
  принимаются.
* **Анонимные перечисления на переменной** (`Command : (NO_CMD := 0, SET_SHIFT := 1)`)
  работают, и их члены видны в блоке без квалификации.
* **Доступ к битам** — `Status.3 := TRUE` — работает для `WORD`/`DWORD`/`BOOL`.
* **Значения по умолчанию в DUT** (`InLow : REAL := 4`) — часть типа: структура
  сразу создаётся с ними, а не с нулями.
* **Константы и инициализаторы в `VAR_GLOBAL`** действительно применяются.
* **`;` после `END_STRUCT`** не требуется, а вот после синонима типа
  (`TYPE T_A : INT`) — требуется, как в стандарте.

## Семантика, приближенная к контроллеру

Ради этого всё и писалось — места, где самодельные интерпретаторы обычно
расходятся с реальным ПЛК и выдают «зелёные» тесты на неверной логике:

- **Переполнение целых заворачивается по разрядности типа.** `INT#32767 + 1`
  даёт `-32768`, `UINT#0 - 1` даёт `65535`, `BYTE#256` даёт `0`.
- **`REAL` хранится как float32**, а не как double, — иначе накопительная
  арифметика разойдётся с контроллером.
- **Округление половин — от нуля** (`2.5 → 3`, `-2.5 → -3`), не банковское.
- **Деление целых усекает к нулю** (`-7 / 2 = -3`), `MOD` берёт знак делимого.
- **Беззнаковый литерал не уже `INT`**: `100 + 100` даёт `200`, а не `-56`.
- **`AND`/`OR` над `BOOL` замыкаются накоротко**, над целыми — побитово.
- **`VAR_IN_OUT` — ссылка**, изменения видны вызывающему.
- **Библиотека из проекта перекрывает встроенную.** `TON`, написанный на ST в
  `Utils.st`, используется вместо стандартного — блок тестируется ровно с тем
  таймером, с которым будет работать.
- **Ошибки с координатой**: у исключения есть `source`, `line`, `col`.

## CLI

```console
$ iecst check valve.st                          # разобрать и слинковать
ok: 1 POU(s), 38 type(s), 2 global(s)
  FUNCTION_BLOCK FB_VALVE

$ iecst run   valve.st --set Cmd=true --scans 3
$ iecst scan  valve.st --set Cmd=true --scans 5 --trace
$ iecst run   valve.st --set Cmd=true --scans 3 --expect Open=true   # код 1 при провале

$ iecst run Tank.st --include Types.st --include Utils.st \
      --set 'Limits={"lo": 0, "hi": 50}' --scans 5
```

Общие ключи: `--pou` (если в файле не один блок), `--include` (библиотека,
повторяемый), `--cycle` (время скана, по умолчанию `10ms`), `--set` (значение
`NAME=VALUE`; структуру можно задать JSON-ом).

## Что поддерживается

| Область | Поддержано |
|---|---|
| Типы | `BOOL`, `SINT..LINT`/`USINT..ULINT`, `BYTE..LWORD`, `REAL`/`LREAL`, `TIME`, `DATE`, `TOD`, `DT`, `STRING`, `CHAR`, `ARRAY` (многомерные), `STRUCT`, `UNION`, `POINTER TO`, `REFERENCE TO`, перечисления (в т.ч. с базовым типом) |
| POU | `FUNCTION_BLOCK`, `FUNCTION` (с рекурсией), `PROGRAM`, `VAR_INPUT/OUTPUT/IN_OUT/VAR/GLOBAL/CONSTANT` |
| Операторы | вся арифметика, `MOD`, `**`, сравнения, `AND/OR/XOR/NOT`, битовые, `SHL/SHR/ROL/ROR` |
| Управление | `IF/ELSIF/ELSE`, `CASE` с диапазонами и списками, `FOR/WHILE/REPEAT`, `EXIT/CONTINUE/RETURN` |
| Стандартные FB | `TON`, `TOF`, `TP`, `R_TRIG`, `F_TRIG`, `CTU`, `CTD`, `CTUD`, `RS`, `SR` |
| Стандартные функции | `ABS`, `SQRT`, `LN/LOG/EXP`, тригонометрия, `MIN/MAX/LIMIT/SEL/MUX/MOVE`, `ADD/SUB/MUL/DIV`, строковые (`LEN/LEFT/RIGHT/MID/CONCAT/INSERT/DELETE/REPLACE/FIND`), конверсии `X_TO_Y` и `INT(x)` |
| Прочее | прагмы `{attribute}`, `AT %IX0.0`, вложенные комментарии, экранирование строк, `ADR`/`REF`/`SIZEOF` |

## Чего нет

Осознанно вне области:

- Объектно-ориентированные расширения CODESYS — `METHOD`, `PROPERTY`,
  `INTERFACE`, `CLASS`, `NAMESPACE`, `ACTION`. Парсер падает с явным
  сообщением, а не молча игнорирует.
- `VAR_INPUT RETAIN`, `VAR_CONFIG` — принимаются синтаксически, не влияют.
- Привязка к железу (`AT %I...`): адрес запоминается, но ввода-вывода нет.
- Блок, *названный* как модификатор (`FUNCTION_BLOCK Final`), отвергается: это
  читалось бы двояко, и выбирать за вас интерпретатор не станет.

## Структура

```
iecst/          сам интерпретатор (пакет, зависимостей нет)
  lexer.py        токенизация
  parser.py       рекурсивный спуск + приоритеты операторов по стандарту
  ast_nodes.py    узлы дерева
  values.py       система типов: заворот целых, ячейки, массивы, структуры
  model.py        слинкованная модель: типы, POU, глобалы
  stdlib.py       стандартные функции и FB
  interpreter.py  исполнение по сканам и виртуальный клок
  api.py          Testbench — фасад для тестов
  cli.py          командная строка
tests/          юнит-тесты интерпретатора (257)
examples/       минимальные примеры, по одному на тему
benchmarks/     тесты на настоящих блоках (46)
```

### Свой блок

1. Положите `.st`-файлы проекта в отдельную папку — как их отдаёт CODESYS.
2. Загрузите папку целиком и создайте стенд:

```python
from pathlib import Path
from iecst import Testbench
from iecst.model import load_sources

SRC = Path("my_project")
model = load_sources({str(p): p.read_text(encoding="utf-8-sig")
                      for p in sorted(SRC.rglob("*.st"))})
tb = Testbench(model, "MyBlock", cycle_time="10ms")
```

3. Пишите тесты по образцу `examples/06_test_a_project.py`.

## Тесты

```console
$ python3 -m unittest discover -s tests -t .             # 257 юнит-тестов
$ python3 examples/01_first_test.py                      # примеры — обычные скрипты
$ python3 benchmarks/analog_input/test_analog_input.py   # реальный блок, 32 теста
```

Зависимостей нет: юнит-тесты идут на встроенном `unittest`, примеры и
бенчмарки — самостоятельные скрипты, каждый со своим `sys.path`. `pytest` тоже
подойдёт — `python3 -m pytest tests/` — но он не требуется.

## Лицензия

MIT.
