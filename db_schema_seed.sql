-- =====================================================================
-- LearnGuard — DB schema seed (toàn bộ 6 bảng + indexes, dạng seedable)
-- Nguồn: pg_dump schema-only từ PostgreSQL đang chạy (learnguard-pg)
-- Ngày xuất: 2026-11-02 · Baseline alembic: 1de0e14023d0
--
-- Bên trong gồm 2 vùng sở hữu (xem PIPELINE_GUIDE.md mục 10):
--   - ml-data:   ml_predictions (M5 upsert; backend chỉ SELECT)
--   - backend:   users, audit_logs, briefs, decisions, llm_calls (Alembic)
--   - alembic_version: đã ghi baseline — backend KHÔNG cần chạy
--     `alembic upgrade head` nữa; migration sau tiếp tục từ baseline.
--
-- Cách nạp (backend chạy 1 lần khi khởi tạo DB):
--   psql -U <user> -d learnguard -f db_schema_seed.sql
-- Lưu ý: dữ liệu prediction KHÔNG nằm trong file này — M5 cronjob sẽ
-- upsert theo lịch; file này chỉ là schema.
-- =====================================================================

--
-- PostgreSQL database dump
--

\restrict BKIwloCMxeSvJpmRJIjSDo7hraJkaDEQyY5mVkFySLyO6FIHg7IOiyh5dM4eBcP

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    log_id bigint NOT NULL,
    user_id bigint,
    action character varying(64) NOT NULL,
    entity character varying(64) NOT NULL,
    entity_id character varying(128),
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_logs_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_logs_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_logs_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_logs_log_id_seq OWNED BY public.audit_logs.log_id;


--
-- Name: briefs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.briefs (
    brief_id bigint NOT NULL,
    student_key character(16) NOT NULL,
    code_module character varying(8) NOT NULL,
    code_presentation character varying(8) NOT NULL,
    version integer NOT NULL,
    content jsonb NOT NULL,
    citations jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(24) DEFAULT 'awaiting_review'::character varying NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: briefs_brief_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.briefs_brief_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: briefs_brief_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.briefs_brief_id_seq OWNED BY public.briefs.brief_id;


--
-- Name: decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decisions (
    decision_id bigint NOT NULL,
    brief_id bigint NOT NULL,
    user_id bigint NOT NULL,
    action character varying(8) NOT NULL,
    feedback text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: decisions_decision_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.decisions_decision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: decisions_decision_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.decisions_decision_id_seq OWNED BY public.decisions.decision_id;


--
-- Name: llm_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_calls (
    call_id bigint NOT NULL,
    brief_id bigint,
    model character varying(128) NOT NULL,
    prompt_tokens integer DEFAULT 0 NOT NULL,
    completion_tokens integer DEFAULT 0 NOT NULL,
    cost_usd numeric(12,6),
    latency_ms integer NOT NULL,
    status character varying(24) NOT NULL,
    called_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: llm_calls_call_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_calls_call_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_calls_call_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_calls_call_id_seq OWNED BY public.llm_calls.call_id;


--
-- Name: ml_predictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ml_predictions (
    student_key character(16) NOT NULL,
    code_module character varying NOT NULL,
    code_presentation character varying NOT NULL,
    split character varying NOT NULL,
    risk_score double precision,
    predicted_label smallint,
    threshold double precision,
    uncertainty double precision,
    status character varying NOT NULL,
    review_reason character varying,
    model_version character varying NOT NULL,
    snapshot_version character varying NOT NULL,
    cutoff_day integer NOT NULL,
    top_signals_json text,
    run_id character varying NOT NULL,
    scored_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id bigint NOT NULL,
    email character varying(255) NOT NULL,
    full_name character varying(255) NOT NULL,
    role character varying(16) NOT NULL,
    password_hash character varying(255) NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_user_id_seq OWNED BY public.users.user_id;


--
-- Name: audit_logs log_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN log_id SET DEFAULT nextval('public.audit_logs_log_id_seq'::regclass);


--
-- Name: briefs brief_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefs ALTER COLUMN brief_id SET DEFAULT nextval('public.briefs_brief_id_seq'::regclass);


--
-- Name: decisions decision_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions ALTER COLUMN decision_id SET DEFAULT nextval('public.decisions_decision_id_seq'::regclass);


--
-- Name: llm_calls call_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls ALTER COLUMN call_id SET DEFAULT nextval('public.llm_calls_call_id_seq'::regclass);


--
-- Name: users user_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN user_id SET DEFAULT nextval('public.users_user_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (log_id);


--
-- Name: briefs briefs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_pkey PRIMARY KEY (brief_id);


--
-- Name: decisions decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_pkey PRIMARY KEY (decision_id);


--
-- Name: llm_calls llm_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_pkey PRIMARY KEY (call_id);


--
-- Name: ml_predictions ml_predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ml_predictions
    ADD CONSTRAINT ml_predictions_pkey PRIMARY KEY (code_module, code_presentation, student_key);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: idx_audit_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_entity ON public.audit_logs USING btree (entity, entity_id);


--
-- Name: idx_audit_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_user ON public.audit_logs USING btree (user_id, at);


--
-- Name: ml_predictions_model_version_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ml_predictions_model_version_idx ON public.ml_predictions USING btree (model_version);


--
-- Name: ml_predictions_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ml_predictions_status_idx ON public.ml_predictions USING btree (status);


--
-- Name: uq_briefs_prediction_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_briefs_prediction_version ON public.briefs USING btree (code_module, code_presentation, student_key, version);


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: briefs briefs_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(user_id);


--
-- Name: decisions decisions_brief_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_brief_id_fkey FOREIGN KEY (brief_id) REFERENCES public.briefs(brief_id);


--
-- Name: decisions decisions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: llm_calls llm_calls_brief_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_brief_id_fkey FOREIGN KEY (brief_id) REFERENCES public.briefs(brief_id);


--
-- PostgreSQL database dump complete
--

\unrestrict BKIwloCMxeSvJpmRJIjSDo7hraJkaDEQyY5mVkFySLyO6FIHg7IOiyh5dM4eBcP

