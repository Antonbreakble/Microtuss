FUNCTION LIMIT_R : REAL
VAR_INPUT
	In : REAL;
	Low : REAL := 0;
	High : REAL := 1;
END_VAR
VAR
END_VAR

// Автор: Antonbreakble

IF In > High THEN
	LIMIT_R := High;
ELSIF In < Low THEN
	LIMIT_R := Low;
ELSE
	LIMIT_R := In;
END_IF