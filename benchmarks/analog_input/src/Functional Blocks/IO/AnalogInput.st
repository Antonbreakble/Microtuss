FUNCTION_BLOCK FINAL AnalogInput
VAR_INPUT
	{attribute 'symbol' := 'none'}
	rIn : REAL; // Данные с IO модуля
	{attribute 'symbol' := 'none'}
	WithDiagnostic : BOOL := TRUE;
	{attribute 'symbol' := 'none'}
	bHealth : BOOL; // Бит целостности канала
	{attribute 'symbol' := 'read'}
	Imitation : BOOL;
	{attribute 'symbol' := 'none'}
	rInImit : REAL; // Вход для данных в режиме имитации
	{attribute 'symbol' := 'read'}	
	Info : AI_Info;
	{attribute 'symbol' := 'readwrite'}	
	PAR : AI_PAR; // Параметры блока
END_VAR

VAR_OUTPUT
	{attribute 'symbol' := 'read'}	
	OUT : AIOut; // Выходные данные
	{attribute 'symbol' := 'none'}
	HHAlarm : BOOL; // Бит превышения HH уставки
	{attribute 'symbol' := 'none'}
	HAlarm : BOOL; // Бит превышения H уставки
	{attribute 'symbol' := 'none'}
	LAlarm : BOOL; // Бит превышения L уставки
	{attribute 'symbol' := 'none'}
	LLAlarm : BOOL; // Бит превышения LL уставки
END_VAR

VAR
	{attribute 'symbol' := 'none'}
	InnerFilter : EmaFilter;
	{attribute 'symbol' := 'readwrite'}
	Command : (NO_CMD := 0, SET_SHIFT := 1, DROP_SHIFT := 2);
	{attribute 'symbol' := 'none'}	
	stCmd : INT;
	{attribute 'symbol' := 'none'}
	IsNegativeOverflow: BOOL;
	{attribute 'symbol' := 'none'}
	IsPositiveOverflow : BOOL;
	{attribute 'symbol' := 'none'}
	InternalError : BOOL;
	{attribute 'symbol' := 'none'}
	FilteredValue : REAL;
END_VAR

VAR_TEMP
	{attribute 'symbol' := 'none'}
	InValue : REAL;
	{attribute 'symbol' := 'none'}
	Deadband : REAL;
	{attribute 'symbol' := 'none'}
	ScaledValue : REAL;
END_VAR

// Автор: Antonbreakble

IF Imitation THEN 
	InValue := rInImit;
ELSE
	InValue := rIn;
END_IF

// Обработки диапазонов сигнала
IsPositiveOverflow := InValue > PAR.InPositiveOverflow;
IsNegativeOverflow := InValue < PAR.InNegativeOverflow;

//Защита от деления на ноль
InternalError := (PAR.InHigh - PAR.InLow) = 0;

// Обработка входного сигнала
IF NOT InternalError THEN 

	ScaledValue := SCALE_R(rIn := InValue,
						   rInLow := PAR.InLow,
						   rInHigh := PAR.InHigh, 
						   rOutLow := PAR.PvLow, 
						   rOutHigh := PAR.PvHigh);
						   
	InnerFilter(In := ScaledValue, 
				Alpha := PAR.FiltrationRate, 
				Out => FilteredValue);
				   
	Deadband := ((PAR.PvHigh - PAR.PvLow) / 100) * LIMIT_R(PAR.DeadbandRate, 0, 100);	
			 
	HHAlarm := FilteredValue >= PAR.HH + Deadband;
    HAlarm := FilteredValue >= PAR.H + Deadband AND FilteredValue < PAR.HH - Deadband;
    LAlarm := FilteredValue <= PAR.L - Deadband AND FilteredValue > PAR.LL + Deadband;
    LLAlarm := FilteredValue <= PAR.LL - Deadband;
				 
END_IF

// Формирование выходной структуры

OUT.Status.0 := (InternalError OR bHealth OR IsPositiveOverflow OR IsNegativeOverflow) AND WithDiagnostic;
OUT.PV := FilteredValue - PAR.PvShift;
OUT.Status.1 := Imitation;
OUT.Status.2 := PAR.FiltrationRate <> 1;
OUT.Status.3 := HHAlarm;
OUT.Status.4 := HAlarm;
OUT.Status.5 := LAlarm;
OUT.Status.6 := LLAlarm;
OUT.Status.7 := FALSE; // RESERVED
OUT.Status.8 := IsPositiveOverflow AND WithDiagnostic;
OUT.Status.9 := FALSE; // RESERVED
OUT.Status.10 := IsNegativeOverflow AND WithDiagnostic;

CASE stCmd OF 
	SET_SHIFT:
		PAR.PvShift := FilteredValue; 
	DROP_SHIFT:
		PAR.PvShift := 0;	
END_CASE

stCmd := Command;
Command := 0;


