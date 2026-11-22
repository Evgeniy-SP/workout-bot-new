-- Run with psql -X -v ON_ERROR_STOP=1 on the verified legacy schema.
BEGIN;

CREATE TABLE public.achievements (
    id serial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
    code varchar(32) NOT NULL,
    unlocked_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_achievement_user_code UNIQUE (user_id, code)
);

CREATE INDEX ix_achievements_user_id ON public.achievements (user_id);
CREATE INDEX ix_session_exercise_notes_session_id ON public.session_exercise_notes (session_id);
CREATE INDEX ix_session_exercise_notes_exercise_id ON public.session_exercise_notes (exercise_id);

COMMIT;
