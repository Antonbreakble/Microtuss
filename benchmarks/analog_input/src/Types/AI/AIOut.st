TYPE AIOut :
	STRUCT
		{attribute 'symbol' := 'read'}
		PV : REAL; // Выходное значение
		{attribute 'symbol' := 'read'}
		Status : DWORD;
		// 0 - Общая неисправность 
		// 1 - Режим имитации включен
		// 2 - Фильтрация включена
		// 3 - HH
		// 4 - H
		// 5 - L
		// 6 - LL
	END_STRUCT
END_TYPE
