TYPE AI_PAR :
	STRUCT
		{attribute 'symbol' := 'readwrite'}	
		FiltrationRate : REAL := 1;
		{attribute 'symbol' := 'read'}
		PvShift : REAL := 0; // Сдвиг выходной велечины
		{attribute 'symbol' := 'none'}	
		InLow : REAL := 4;	//Нижний предел входной величины
		{attribute 'symbol' := 'none'}	
		InHigh : REAL := 20; //Верхний предел входной величины
		{attribute 'symbol' := 'none'}	
		InPositiveOverflow : REAL := 22.5; //Верхний предел переполнения входной величины
		{attribute 'symbol' := 'none'}	
		InNegativeOverflow : REAL := 3.7; //Нижний предел переполнения входной величины
		{attribute 'symbol' := 'readwrite'}	
		PvLow : REAL := 0; // Нижний предел выходной величины
		{attribute 'symbol' := 'readwrite'}	
		PvHigh : REAL := 100; // Верхний предел выходной величины
		{attribute 'symbol' := 'readwrite'}	
		LL : REAL := 20; // Нижний критический предел
		{attribute 'symbol' := 'readwrite'}	
		L : REAL := 40; // Нижний предупредительный предел
		{attribute 'symbol' := 'readwrite'}	
		H : REAL := 60; // Верхний предупредительный предел
		{attribute 'symbol' := 'readwrite'}	
		HH : REAL := 80; // Верхний критический предел
		{attribute 'symbol' := 'readwrite'}
		DeadbandRate : REAL := 0; // Зона нечувствительности [0..100]
	END_STRUCT
END_TYPE
