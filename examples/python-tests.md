# Как писать тесты на Python

Практическое руководство по `iecst` для случая, когда блок надо прогнать из
Python. От «подать вход и проверить выход» до DUT, библиотек и разбора ошибок.

Для запуска нужен Python 3.11+. Зависимостей нет.

```console
$ pip install -e .
$ python3 examples/01_first_test.py
```

Рядом лежат рабочие примеры — по одному на тему, их можно запускать и править:
[`examples/README.md`](README.md). Здесь те же вещи разобраны подробнее и в
порядке возрастания сложности.

Разделы читаются подряд: `tb` в следующем фрагменте — это стенд из
предыдущего, если рядом не сказано, из какого он проекта. Отдельный фрагмент
без окружения не запустится — это выдержка, а не файл. Готовые файлы целиком —
в [`examples/`](README.md).

---

## Содержание

1. [Минимальный тест](#1-минимальный-тест)
2. [Как устроен скан и время](#2-как-устроен-скан-и-время)
3. [Чтение значений](#3-чтение-значений)
4. [Запись значений](#4-запись-значений)
5. [`expect`: проверка без исключений](#5-expect-проверка-без-исключений)
6. [Что подавать на вход: DUT, массивы, перечисления, TIME](#6-что-подавать-на-вход)
7. [`VAR_IN_OUT`](#7-var_in_out)
8. [Вызов `FUNCTION`](#8-вызов-function)
9. [Загрузка проекта: файлы, папка, библиотеки](#9-загрузка-проекта)
10. [`Session` — движок без тестов](#10-session--движок-без-тестов)
11. [Ручной прогон экземпляра FB](#11-ручной-прогон-экземпляра-fb)
12. [Организация набора тестов](#12-организация-набора-тестов)
13. [Когда тест падает: каталог ошибок](#13-когда-тест-падает)

---

## 1. Минимальный тест

Блок `examples/valve.st`: пока `Cmd` не совпадает с текущим положением, идёт
отсчёт `TravelTime`, после чего выход меняется.

```python
from iecst import Testbench

tb = Testbench.from_file("examples/valve.st", "FB_Valve", cycle_time="10ms")

tb.scan(Cmd=True)     # подать вход и выполнить один скан
tb.run(200)           # ещё 200 сканов = 2 с виртуального времени

tb.expect(Open=True, Busy=False)
print(tb.summary())
```

```console
FB_Valve: 2/2 expectations passed
```

Четыре метода покрывают почти всё:

| Метод | Что делает |
|---|---|
| `Testbench.from_file(путь, POU, cycle_time=...)` | загрузить `.st` и выбрать блок |
| `scan(**входы)` | записать входы и выполнить **один** скан |
| `run(n)` | выполнить ещё `n` сканов |
| `get(имя)` | прочитать значение |

`scan` — это сокращение для `set(...)` + `step()`. `step()` делает один скан
без записи входов.

---

## 2. Как устроен скан и время

Тело блока исполняется один раз за скан. `cycle_time` говорит, сколько времени
означает один скан, и **это единственный источник времени** — `TON` считает от
шага скана, а не от системных часов.

```python
tb = Testbench.from_file("examples/valve.st", "FB_Valve", cycle_time="100ms")
tb.scan(Cmd=True)
print(tb.scans)   # 1        — сколько сканов сделано
print(tb.now)     # 100000000 — виртуальное время, наносекунды
```

Отсюда следствие, которое ловит новичков: **скан, запустивший таймер, отдаёт
`ET = 0`**. Таймер на `T#2s` при скане 100 мс отработает не на 20-м скане, а на
21-м:

| Скан | t | ET | Open |
|---|---|---|---|
| 1 | 100 ms | 0 ms | False |
| 20 | 2000 ms | 1900 ms | False |
| 21 | 2100 ms | 2000 ms | **True** |

Поэтому в тестах с таймерами число сканов лучше писать с пояснением:

```python
SCANS_TO_TRAVEL = 21   # T#2s при скане 100мс, плюс скан, который стартует таймер
tb.run(SCANS_TO_TRAVEL)
tb.expect(Open=True)
```

`cycle_time` принимает `"10ms"`, `"1s"`, `"500us"`, `"T#50ms"` или просто число
наносекунд. Разные тесты могут брать разный шаг — это не глобальная настройка.

Разберите пример [`examples/02_timers.py`](02_timers.py) — он печатает таблицу
выше и проверяет обе стороны перехода.

---

## 3. Чтение значений

`get(имя)` возвращает значение переменной. Имя — как в ST: точка достаёт
вложенное, индексы пишутся как в коде. Ниже — по одному примеру на каждый вид
пути; блоки разные, и брать их надо из того проекта, где они объявлены:

```python
tb.get("Open")            # True          — выход блока (valve.st)
tb.get("travel.ET")       # 2000000000    — таймер внутри блока, в наносекундах
tb.get("travel.Q")        # True          — вложенный FB читается по пути
tb.get("Limits.Lo")       # 10.0          — поле DUT (project/Types.st)
tb.get("Configs[2].Mode") # 20            — элемент массива, индекс с 1, как в ST
tb.get("m[2,3]")          # 5             — многомерный массив
```

Первые три строки — `examples/valve.st`, остальные три — примеры путей:
`Limits.Lo` объявлен в [`examples/project`](project), `Configs[2].Mode` — в
[`benchmarks/drive`](../benchmarks/drive), `m` — синтетический `ARRAY[1..2, 1..3]`.

Значения `travel.ET` и `travel.Q` — это пик таймера, скан 21 из таблицы выше:
на скане 22 он уже сброшен (`ET = 0`), потому что `travel.IN` стал `False`.
Многомерный индекс можно писать и как `m[2,3]`, и как `m[2][3]` — но целиком в
**одной** строке имени: `get("m[2][3]")` работает, а `get("m[2]")[3]` — нет,
потому что `get` возвращает значение, а не ячейку.

> **Доступ к битам** (`Status.3`) работает **внутри ST** — в коде блока. Из
> Python через `get`/`set` бит не читается: путь понимает индексы массивов, а
> не битовые срезы. Читайте слово целиком и разбирайте его сами:
> `bool((tb.get("OUT.STATUS") >> 3) & 1)`. Так и сделано в
> [`benchmarks/analog_input`](../benchmarks/analog_input).

`get` отдаёт «сырое» значение: `BOOL` → `bool`, `REAL` → `float`,
`INT` → `int`, `TIME` → **целое число наносекунд**, перечисление → порядковый
номер.

Для перечислений и `TIME` это неудобно читать, поэтому есть `state()` — снимок
всех переменных в удобном виде:

```python
tb.state()
# {'CMD': True, 'TRAVELTIME': 'T#2s', 'OPEN': True, ...
#  'STATE': 'OPEN', 'TRAVEL': {'IN': True, 'PT': 'T#2s', 'Q': True, 'ET': 'T#2s'}}
```

| Что | `get()` | `state()` |
|---|---|---|
| `TIME` | `2000000000` | `'T#2s'` |
| перечисление | `1` | `'OPEN'` |
| `REAL` | `50.0` | `50.0` |
| `BOOL` | `True` | `True` |

Правило простое: `get` — для сравнений в коде, `state` — для вывода на экран и
для проверки «всё сразу». Для отладочной печати всего состояния удобен
`tb.runtime.report()`.

---

## 4. Запись значений

Три формы, все равнозначны:

```python
tb.set(Fill=True, Level=50.0)     # keyword-форма, несколько сразу
tb.set("Fill", True)              # имя и значение
tb.set_many({"Fill": True})       # словарь
```

Имена сверяются без учёта регистра, как в ST: `tb.set(Fill=True)` и
`tb.set(FILL=True)` — одно и то же.

**Писать можно не только во входы.** `VAR_OUTPUT` и `VAR` тоже записываются —
это приём тестирования, а не интерфейс блока (см. раздел 10):

```python
tb.set("Percent", 42.0)     # подложить значение в выход
tb.set("settle.ET", 0)      # сбросить поле вложенного таймера
```

**Опечатка не проходит молча.** Проверяются и имя, и тип, и границы:

```
no variable named 'LEVELS'
cannot convert 'high' to REAL
array index 5 out of range 1..2 for ARRAY[1..2] OF T_R
```

---

## 5. `expect`: проверка без исключений

`expect` **не бросает исключение**. Он записывает результат и возвращает
`True`/`False`, так что один прогон сообщает сразу обо всех расхождениях:

```python
ok = tb.expect(State="FULL", Percent=100.0)   # False
print(tb.summary())
```

```console
FB_Tank: 0/2 expectations passed
  [FAIL] STATE: expected FULL, got FILLING  (at scan 1, t=T#100ms)
  [FAIL] PERCENT: expected 100.0, got 106.25  (at scan 1, t=T#100ms)
```

Обратите внимание: перечисление напечатано **именем члена**, а не числом —
`expected FULL, got FILLING` читается, `expected 2, got 1` нет. Для этого
`expect` прогоняет значения через тот же декодер, что и `set`, поэтому
`expect(State="FULL")` эквивалентно `expect(State=2)`.

Провалившиеся проверки лежат в `tb.failures`:

```python
for failure in tb.failures:
    print(failure.name, failure.expected, failure.actual, failure.scanned, failure.time_ns)
```

Когда падать надо сразу — используйте обычный `assert`:

```python
assert tb.get("Open") is True
self.assertEqual("FULL", tb.state()["STATE"])   # в unittest
```

`expect` уместен там, где хочется увидеть **все** расхождения за прогон;
`assert` — где первое падение уже всё объясняет.

---

## 6. Что подавать на вход

Значения записываются «полевыми» записями, знакомыми по CODESYS. Работает на
верхнем уровне и **внутри** структур и массивов.

### Перечисления

Имя члена, в любом регистре, с квалификацией или без:

```python
tb.set(State="Open")
tb.set(State="E_ValveState#Open")    # то же самое
tb.set(State="open")                 # тоже
tb.set(State=1)                      # можно и числом
```

Опечатка называет допустимые варианты:

```
'FULLISH' is not a member of E_FILL (expected one of: IDLE, FILLING, FULL, OVERFLOW)
```

### TIME

Строкой в привычном виде или числом наносекунд:

```python
tb.set(TravelTime="2s")
tb.set(TravelTime="500ms")
tb.set(TravelTime="1m30s")           # 90 секунд
tb.set(TravelTime="T#1s500ms")       # форма CODESYS
tb.set(TravelTime=2_000_000_000)     # наносекунды
```

### DUT целиком — словарём

Структуру можно задать одним словарём; поля, которых нет, сохраняют значения
по умолчанию из объявления типа:

```python
tb.set(Limits={"lo": 0.0, "hi": 50.0})
```

Ключи — без учёта регистра. Опечатка в имени поля останавливает тест:

```
'T_LIMITS' has no member(s) LOL (expected: HI, LO)
```

Это работает и для вложенных структур, и для полей-перечислений, и для
`TIME` внутри DUT:

```python
tb.set(Cfg={"mode": "Automatic",              # член перечисления
            "speedrange": {"lo": 0.0, "hi": 1500.0},   # вложенная структура
            "ramptime": "200ms"})             # TIME строкой
```

### Массивы и элементы по пути

```python
tb.set("Configs[2].Mode", "Automatic")   # элемент массива DUT
tb.set("Matrix[2,3]", 5)                 # многомерный, m[2][3] — то же самое
tb.set("travel.PT", "1s")                # поле вложенного FB
```

Нижняя граница берётся из объявления (`ARRAY[1..2]` — индекс с 1), выход за
границы — ошибка.

---

## 7. `VAR_IN_OUT`

`VAR_IN_OUT` — это **ссылка** на чужую память, а не копия. У блока под тестом
своей памяти нет, поэтому стенд создаёт её сам, и до неё можно дотянуться:

```python
tb.runtime.bind("Position")          # ячейка, за которой стоит параметр
tb.get("Position")                   # 0 — началось с нуля
tb.scan(Start=True)
tb.get("Position")                   # блок её менял — видно снаружи
```

`bind` возвращает **ячейку**, а не значение. Второй аргумент решает, что с ней
делать:

```python
tb.runtime.bind("Position")          # просто получить ячейку
tb.runtime.bind("Position", 42)      # записать в неё значение
tb.runtime.bind("Position", cell)    # сделать ссылкой на чужую ячейку
```

Последняя форма связывает два блока — выход одного подать во `VAR_IN_OUT`
другого:

```python
cell = producer.runtime.bind("pos")     # ячейка первого блока
consumer.runtime.bind("pos", cell)      # второй блок пишет в ту же память

producer.step()
consumer.step()
consumer.get("seen")                    # видит то, что записал первый
```

Различие «значение или ячейка» — не мелочь: оно ровно то же, что делает язык,
когда один блок вызывает другой и передаёт ему переменную как `VAR_IN_OUT`.
Привязка ячейки к переменной, которая не `VAR_IN_OUT`, отвергается с
объяснением.

У **вложенного** блока непривязанный `VAR_IN_OUT` — ошибка, а не пустышка: так
же повёл бы себя контроллер. Это ловится при первом скане.

---

## 8. Вызов `FUNCTION`

`FUNCTION` не имеет экземпляра и состояния, поэтому вызывается напрямую, без
сканов. Аргументы — по именам из объявления:

```python
tb.runtime.call("Pct", v=25.0, lo=0.0, hi=50.0)   # 50.0
```

Параметры со значением по умолчанию можно опустить — в `examples/project/`
функция `Pct` объявлена с `lo := 0.0; hi := 100.0`:

```python
tb.runtime.call("Pct", v=25.0)                    # 25.0 — границы по умолчанию
```

Так удобно тестировать вычислительные функции отдельно от блока, который их
использует. В `Session` то же самое — `session.call("Pct", ...)`.

---

## 9. Загрузка проекта

Реальный проект CODESYS — это папка, где каждый объект в своём файле: блок,
DUT, библиотека. Порядок файлов не важен, типы и POU линкуются вместе.

### Папка целиком

```python
tb = Testbench.from_folder("my_project", "MyBlock", cycle_time="10ms")
```

Загружаются все `.st` под папкой, рекурсивно. Имя блока нужно указывать почти
всегда: в папке обычно есть и библиотечные блоки (`TON` и прочие), поэтому
автоматически угадать единственный не выйдет — при неоднозначности ошибка
перечислит кандидатов.

### Файл плюс библиотеки

Когда файлов немного, их перечисляют явно. Библиотеки идут **первыми**:

```python
tb = Testbench.from_file(
    "fb_drive.st", "FB_Drive",
    includes=["duts.st", "utils.st"],
    cycle_time="10ms",
)
```

Библиотека, написанная на ST, **перекрывает** встроенную реализацию с тем же
именем. Это важно: если в проекте есть свой `TON`, блок будет тестироваться
именно с ним, а не со стандартным.

### Из строк

Когда исходники не на диске — например, пришли из базы или редактора:

```python
from iecst import Testbench, load_sources

model = load_sources({"a.st": "...", "b.st": "..."})
tb = Testbench(model, "MyBlock", cycle_time="10ms")
```

### Быстрая проверка сценария

Если нужен не набор тестов, а один прогон — `run_scenario`:

```python
from iecst import run_scenario

state = run_scenario(
    "Tank.st", "FB_Tank",
    includes=["Types.st", "Utils.st"],
    steps=[({"Fill": True}, 5), ({"Level": 95.0}, 10)],
)
print(state["STATE"])
```

---

## 10. `Session` — движок без тестов

`Testbench` — надстройка над `Session`. Последняя умеет всё то же, но не знает
про ожидания, ничего не печатает и умеет описывать интерфейс блока. Это то, на
чём строится управление блоком из кода, CLI и будущий GUI.

```python
from iecst import Session

s = Session.from_folder("examples/project", "FB_Tank", cycle_time="100ms")

s.set("Fill", True)
s.step()
s.get("State")         # 1 — сырое значение перечисления
s.snapshot()           # {'FILL': True, 'LEVEL': 50.0, 'STATE': 'FILLING', ...}
s.scans, s.now         # сколько сканов, сколько виртуального времени
s.run(10)
s.call("Pct", v=25.0, lo=0.0, hi=50.0)   # 50.0
```

Всё, что делает `Testbench`, здесь есть с теми же именами; отличается только
`expect`/`summary` — их в `Session` нет.

### Описание интерфейса блока

Главное отличие от `Testbench` — можно спросить, что у блока на входе и выходе.
Это то, из чего строится форма:

```python
s.pou_name    # 'FB_Tank'
s.pou_kind    # 'FUNCTION_BLOCK'

for v in s.inputs():
    print(v.name, v.type_name, v.enum_members)
# FILL         BOOL      {}
# LEVEL        REAL      {}
# LIMITS       T_LIMITS  {}
# SETTLETIME   TIME      {}

for v in s.outputs():
    print(v.name, v.type_name)
# STATE        E_FILL
# PERCENT      REAL
# OVERFLOWING  BOOL
```

`Variable` несёт: `name`, `type`, `type_name`, `direction`
(`input`/`output`/`inout`/`local`), `writable`, `enum_members` (для
перечислений — словарь «имя → значение», готовый для выпадающего списка),
и флаги `is_time` / `is_bool` / `is_struct`.

`s.variables()` возвращает все переменные, включая внутренние. Локальные тоже
помечены записываемыми: подложить значение в `VAR` — законный приём
тестирования.

### Декодирование без побочных эффектов

Если нужно перевести то, что ввёл пользователь, не записывая:

```python
s.encode_for("State", "Full")   # 2   — строка -> номер члена
s.display("State", 2)           # 'FULL' — номер -> имя члена
```

---

## 11. Ручной прогон экземпляра FB

Иногда надо погонять конкретный FB отдельно — например, проверить
библиотечный `TON` сам по себе. Экземпляр создаётся явно, а скан выполняется
вызовом `execute_instance`:

```python
from pathlib import Path
from iecst import Runtime, load_sources

source = Path("examples/project/Utils.st").read_text(encoding="utf-8-sig")
model = load_sources({"u.st": source})
rt = Runtime(model, "TON", cycle_time="10ms")

inst = rt.instantiate(model.type_of("TON"))
inst.vars["PT"].value = 200_000_000        # T#200ms в наносекундах

for scan in range(1, 22):
    inst.vars["IN"].value = True
    rt.execute_instance(inst)              # один скан экземпляра
    rt.now += rt.cycle_ns                  # продвинуть виртуальное время
    if scan in (1, 20, 21):
        print(scan, inst.vars["Q"].value, inst.vars["ET"].value)
```

```
1 False 0
20 False 190000000
21 True 200000000
```

Две вещи, на которые стоит посмотреть. Первый скан отдаёт `ET = 0` — та же
задержка таймера, что и в разделе 2, так что `T#200ms` при скане 10 мс
отрабатывает на 21-м скане, а не на 20-м. И `rt.now += rt.cycle_ns` здесь
приходится продвигать руками: `rt.step()` делает это за вас, но он исполняет
POU под тестом, а не произвольный экземпляр.

Внутренние переменные лежат в `inst.vars` как ячейки; `.value` — само
значение. Загружать надо **весь** проект: `benchmarks/drive/src/utils.st`
содержит `FB_ClampRange`, которой нужен тип `T_Range` из соседнего `duts.st`,
и поодиночке этот файл не соберётся.

---

## 12. Организация набора тестов

`Testbench` — обычный объект, поэтому подходит любой раннер: `unittest`,
`pytest`, простые `assert`. Зависимостей у пакета нет, `unittest` встроен.

Проверенный каркас — [`examples/06_test_a_project.py`](06_test_a_project.py),
его стоит взять за образец:

```python
import unittest
from pathlib import Path
from iecst import Testbench

HERE = Path(__file__).resolve().parent
PROJECT = HERE / "project"


def tank(cycle_time="100ms"):
    """Свой стенд на каждый тест: тесты не делят состояние."""
    return Testbench.from_folder(PROJECT, "FB_Tank", cycle_time=cycle_time)


class TestTankStates(unittest.TestCase):
    def test_idle_when_not_filling(self):
        tb = tank()
        tb.scan(Fill=False)
        self.assertEqual("IDLE", tb.state()["STATE"])

    def test_full_needs_the_level_to_settle(self):
        tb = tank()
        tb.scan(Fill=True, Level=95.0)
        self.assertEqual("FILLING", tb.state()["STATE"])   # SettleTime не истёк

        tb.run(2)
        self.assertEqual("FILLING", tb.state()["STATE"], "2 сканов мало")

        tb.step()
        self.assertEqual("FULL", tb.state()["STATE"], "300 мс прошли")


if __name__ == "__main__":
    unittest.main()
```

```console
$ python3 examples/06_test_a_project.py
$ python3 -m pytest examples/          # если pytest установлен
$ python3 -m unittest discover -s tests -t .
```

Что здесь важно:

- **свой стенд на каждый тест** — состояние не протекает между тестами;
- **имя теста говорит, что проверяется**, а не «test1»;
- **проверяются выходы блока**, а не его внутренние переменные;
- **число сканов поясняется арифметикой** таймера, а не берётся «на глаз».

Дорогую часть — разбор проекта — можно сделать один раз и переиспользовать
модель. Экземпляр при этом создаётся свой на каждый тест:

```python
from iecst import Testbench, load_sources

MODEL = load_sources({str(p): p.read_text(encoding="utf-8-sig")
                      for p in sorted(PROJECT.glob("*.st"))})

def tank():
    return Testbench(MODEL, "FB_Tank", cycle_time="100ms")
```

Если тест проверяет одно поле из нескольких — удобен `subTest`:

```python
def test_percent_follows_the_level(self):
    for level, expected in ((10.0, 0.0), (50.0, 50.0), (90.0, 100.0)):
        with self.subTest(level=level):
            tb = tank()
            tb.scan(Fill=True, Level=level)
            self.assertEqual(expected, tb.get("Percent"))
```

---

## 13. Когда тест падает

Ошибки имён и типов бросаются сразу — молчаливого приведения не бывает, потому
что тест, тихо проходящий на опечатке, хуже падающего. Тексты такие:

| Ситуация | Сообщение |
|---|---|
| опечатка в переменной | `no variable named 'LEVELS'` |
| опечатка в поле DUT | `'T_LIMITS' has no member(s) LOL (expected: HI, LO)` |
| неизвестный член перечисления | `'FULLISH' is not a member of E_FILL (expected one of: IDLE, FILLING, FULL, OVERFLOW)` |
| не тот тип значения | `cannot convert 'high' to REAL` |
| индекс вне границ | `CONFIGS[5].MODE: array index 5 out of range 1..2 for ARRAY[1..2] OF T_DRIVECONFIG` |
| обращение с индексом к скаляру | `cannot index 'LEVEL': it is not an array` |
| спуск в не-структуру | `cannot descend into 'HI' of 'LEVEL.HI'` |
| неизвестный блок | `no POU named 'NoBlock' (known: FB_TANK, PCT, TON)` |
| деление на ноль во время скана | `division by zero` |
| синтаксис в `.st` | `bad.st:3:6: expected an expression, found ';'` |

Ошибки пути (`CONFIGS[5].MODE`, спуск в не-структуру) называют **весь** путь, а
не только сломанное звено — по одному сообщению видно, где именно промах.

У исключений есть координата: `IecSyntaxError` несёт `source`, `line`, `col`,
а `format()` печатает их одной строкой.

Исключения устроены иерархично, поэтому ловить можно группой:

```python
from iecst import IecError, IecSyntaxError, IecTypeError, IecNameError

try:
    tb.set(Limits={"lol": 1.0})
except IecTypeError as exc:
    print(exc.message)     # 'T_LIMITS' has no member(s) LOL ...
```

```
IecError
├── IecSyntaxError     разбор .st
├── IecNameError       нет такого имени
├── IecTypeError       значение не того типа
└── IecRuntimeError    ошибка во время скана (например, деление на ноль)
```

### «А мой экспорт вообще загрузился?»

Если нужен не запуск, а проверка исходников — `check`. Он **не бросает**, а
возвращает список проблем, поэтому годится и для отчёта, и для проверки в CI:

```python
from iecst.core.session import check

problems = check("my_project")                  # папка целиком
problems = check(["FB_AI.st", "Types.st"])      # или список файлов
if problems:
    for problem in problems:
        print(problem.format())    # 'bad.st:2:6: expected an expression, found ...'
```

Пустой список — всё разобралось, слинковалось и типы известны. Синтаксическая
ошибка и неизвестный тип возвращаются одинаково, списком; папка читается так же,
как в `Session.from_folder`. Это то, что делает команда `iecst check`:

```console
$ iecst check my_project/
$ iecst run my_project/ --pou FB_Tank --set Fill=true --scans 2
```

Разбор ошибок вживую — [`examples/08_mistakes.py`](08_mistakes.py).

---

## Куда дальше

| Тема | Где смотреть |
|---|---|
| Рабочие примеры по темам | [`examples/`](README.md) |
| Реальный блок из CODESYS | [`benchmarks/analog_input/`](../benchmarks/analog_input) |
| DUT, библиотека, свой `TON` | [`benchmarks/drive/`](../benchmarks/drive) |
| Что поддерживает язык | [README](../README.md#что-поддерживается) |
| Семантика, приближенная к ПЛК | [README](../README.md#семантика-приближенная-к-контроллеру) |
