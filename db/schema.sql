-- BloodConnect schema for PostgreSQL.
--
-- The app creates missing tables automatically on start-up, so running this file is optional.
-- It documents the schema and can be used to provision a database by hand:
--   psql "$DATABASE_URL" -f db/schema.sql
--
-- Only the Flask server connects to the database. Give it a dedicated role that owns (or has
-- read/write access to) these tables, and never expose the database port publicly.

create table if not exists hospitals (
    id             bigserial primary key,
    name           varchar(160) not null unique,
    email          varchar(254) not null unique,
    password_hash  varchar(255) not null,
    phone          varchar(20)  not null,
    address        varchar(255) not null,
    city           varchar(80)  not null,
    state          varchar(80)  not null,
    pincode        varchar(10)  not null,
    country        varchar(80)  not null default 'India',
    hospital_type  varchar(20)  not null,
    latitude       double precision not null,
    longitude      double precision not null,
    is_verified    boolean not null default false,
    created_at     timestamp not null default (now() at time zone 'utc'),
    constraint ck_hospital_latitude check (latitude between -90 and 90),
    constraint ck_hospital_longitude check (longitude between -180 and 180)
);
create index if not exists ix_hospitals_email on hospitals (email);

create table if not exists donors (
    id                  bigserial primary key,
    name                varchar(120) not null,
    email               varchar(254) not null unique,
    password_hash       varchar(255) not null,
    phone               varchar(20),
    date_of_birth       date not null,
    gender              varchar(10) not null,
    weight_kg           double precision not null,
    blood_group         varchar(3) not null,
    health_issues       varchar(20) not null default 'none',
    latitude            double precision not null,
    longitude           double precision not null,
    is_available        boolean not null default true,
    last_donation_date  date,
    created_at          timestamp not null default (now() at time zone 'utc'),
    constraint ck_donor_weight_positive check (weight_kg > 0),
    constraint ck_donor_latitude check (latitude between -90 and 90),
    constraint ck_donor_longitude check (longitude between -180 and 180)
);
create index if not exists ix_donors_email on donors (email);
create index if not exists ix_donors_blood_group on donors (blood_group);
create index if not exists ix_donors_location on donors (latitude, longitude);

create table if not exists blood_requests (
    id                 bigserial primary key,
    hospital_id        bigint not null references hospitals (id) on delete cascade,
    patient_name       varchar(120) not null,
    patient_gender     varchar(10) not null,
    patient_weight_kg  double precision,
    blood_group        varchar(3) not null,
    units_required     integer not null,
    units_pledged      integer not null default 0,
    urgency            varchar(10) not null,
    clinical_notes     varchar(500),
    status             varchar(12) not null default 'open',
    created_at         timestamp not null default (now() at time zone 'utc'),
    closed_at          timestamp,
    constraint ck_request_units_required check (units_required between 1 and 50),
    constraint ck_request_units_pledged check (units_pledged >= 0 and units_pledged <= units_required)
);
create index if not exists ix_blood_requests_hospital_id on blood_requests (hospital_id);
create index if not exists ix_blood_requests_blood_group on blood_requests (blood_group);
create index if not exists ix_blood_requests_status on blood_requests (status);

create table if not exists pledges (
    id          bigserial primary key,
    request_id  bigint not null references blood_requests (id) on delete cascade,
    donor_id    bigint not null references donors (id) on delete cascade,
    status      varchar(12) not null default 'pledged',
    created_at  timestamp not null default (now() at time zone 'utc'),
    updated_at  timestamp not null default (now() at time zone 'utc'),
    -- Trip progress from the donor's directions page (never their coordinates).
    eta_minutes            integer,
    distance_remaining_km  double precision,
    progress_updated_at    timestamp,
    constraint uq_pledge_request_donor unique (request_id, donor_id)
);
create index if not exists ix_pledges_request_id on pledges (request_id);
create index if not exists ix_pledges_donor_id on pledges (donor_id);

create table if not exists notifications (
    id          bigserial primary key,
    request_id  bigint not null references blood_requests (id) on delete cascade,
    donor_id    bigint not null references donors (id) on delete cascade,
    sent_at     timestamp not null default (now() at time zone 'utc'),
    constraint uq_notification_request_donor unique (request_id, donor_id)
);
create index if not exists ix_notifications_request_id on notifications (request_id);
create index if not exists ix_notifications_donor_id on notifications (donor_id);

-- Failed logins per account email, shared by every app process (serverless hosts run several).
create table if not exists login_throttles (
    key                varchar(64) primary key,  -- sha256 of "kind:email"
    failures           integer not null default 0,
    window_started_at  timestamp not null default (now() at time zone 'utc'),
    locked_until       timestamp
);
