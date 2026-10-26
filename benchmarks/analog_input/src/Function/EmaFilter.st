FUNCTION_BLOCK PUBLIC FINAL EmaFilter
VAR_INPUT
	In : REAL; // Входное значение
	Alpha : REAL; // Коэффициент фильтрации [0..1]
END_VAR

VAR_OUTPUT
	Out : REAL; // Фильтрованное значение
END_VAR

VAR_TEMP
	AlphaLimited : REAL;
END_VAR

// Exponential Moving Average, EMA
// aka Фильтр Калмана (нет)
// https://en.wikipedia.org/wiki/Exponential_smoothing

// Автор: Antonbreakble

AlphaLimited := LIMIT_R(Alpha, 0, 1);
Out := In * AlphaLimited + ((1 - AlphaLimited) * Out);
