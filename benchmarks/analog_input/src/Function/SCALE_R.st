FUNCTION SCALE_R : REAL
VAR_INPUT
	rIn : REAL;
	rInLow : REAL;
	rInHigh : REAL;
	rOutLow : REAL;
	rOutHigh : REAL;
END_VAR
VAR
END_VAR

// Автор: Antonbreakble

IF rInHigh = rInLow THEN
	SCALE_R := 0;
ELSE
	SCALE_R := (((rIn - rInLow) * (rOutHigh - rOutLow)) / (rInHigh - rInLow)) + rOutLow;
END_IF