-- Эталонная схема БД workout-bot-new,
-- сгенерирована из app/db/models.py (источник истины — модели,
-- init_db() создаёт таблицы через Base.metadata.create_all).
-- Для боевого восстановления используй pg_dump рабочей БД.

CREATE TABLE users (
	id SERIAL NOT NULL, 
	tg_id BIGINT NOT NULL, 
	username VARCHAR(64), 
	full_name VARCHAR(128) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE workout_types (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description TEXT, 
	sort_order INTEGER NOT NULL, 
	is_archived BOOLEAN NOT NULL, 
	is_in_rotation BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE exercises (
	id SERIAL NOT NULL, 
	workout_type_id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	sets INTEGER, 
	reps INTEGER, 
	weight_kg VARCHAR(32), 
	duration_seconds INTEGER, 
	is_optional BOOLEAN NOT NULL, 
	notes TEXT, 
	sort_order INTEGER NOT NULL, 
	is_archived BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workout_type_id) REFERENCES workout_types (id) ON DELETE CASCADE
);

CREATE TABLE workout_sessions (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	workout_type_id INTEGER NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	notes TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(workout_type_id) REFERENCES workout_types (id) ON DELETE RESTRICT
);

CREATE TABLE session_exercise_notes (
	id SERIAL NOT NULL, 
	session_id INTEGER NOT NULL, 
	exercise_id INTEGER NOT NULL, 
	note TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES workout_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(exercise_id) REFERENCES exercises (id) ON DELETE CASCADE
);

CREATE TABLE session_sets (
	id SERIAL NOT NULL, 
	session_id INTEGER NOT NULL, 
	exercise_id INTEGER NOT NULL, 
	set_number INTEGER NOT NULL, 
	reps INTEGER, 
	weight_kg VARCHAR(32), 
	duration_seconds INTEGER, 
	completed BOOLEAN NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES workout_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(exercise_id) REFERENCES exercises (id) ON DELETE RESTRICT
);

CREATE TABLE session_skipped_exercises (
	id SERIAL NOT NULL, 
	session_id INTEGER NOT NULL, 
	exercise_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES workout_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(exercise_id) REFERENCES exercises (id) ON DELETE CASCADE
);
