drop table if exists audit_events;
drop table if exists bulk_events;
drop table if exists document_blobs;
drop table if exists order_items;
drop table if exists orders;
drop table if exists user_roles;
drop table if exists roles;
drop table if exists app_users;
drop table if exists tenants;

create table tenants (
  tenant_id integer primary key,
  tenant_code varchar(32) not null unique,
  display_name varchar(120) not null,
  active boolean not null,
  created_at timestamp without time zone not null
);

create table app_users (
  user_id bigint primary key,
  tenant_id integer not null references tenants(tenant_id),
  email varchar(255) not null unique,
  full_name varchar(120) not null,
  birth_date date null,
  login_count integer not null,
  credit_limit numeric(14,2) not null,
  profile_json jsonb null,
  user_uuid uuid not null,
  created_at timestamp without time zone not null,
  updated_at timestamp with time zone not null
);

create table roles (
  role_id integer primary key,
  role_code varchar(50) not null unique,
  description text null
);

create table user_roles (
  user_id bigint not null references app_users(user_id),
  role_id integer not null references roles(role_id),
  granted_at timestamp without time zone not null,
  primary key (user_id, role_id)
);

create table orders (
  order_id bigint primary key,
  user_id bigint not null references app_users(user_id),
  order_no varchar(40) not null unique,
  status varchar(20) not null,
  total_amount numeric(16,2) not null,
  ordered_at timestamp with time zone not null,
  shipped_at timestamp without time zone null
);

create table order_items (
  order_id bigint not null references orders(order_id),
  line_no integer not null,
  sku varchar(80) not null,
  quantity integer not null,
  unit_price numeric(12,2) not null,
  discount_rate numeric(5,4) not null,
  primary key (order_id, line_no)
);

create table document_blobs (
  document_id bigint primary key,
  user_id bigint not null references app_users(user_id),
  file_name varchar(180) not null,
  mime_type varchar(100) not null,
  content_text text null,
  content_bytes bytea null,
  metadata_json jsonb null,
  created_at timestamp without time zone not null
);

create table bulk_events (
  event_id bigint primary key,
  tenant_id integer not null references tenants(tenant_id),
  event_type varchar(40) not null,
  payload_text text not null,
  payload_json jsonb null,
  occurred_at timestamp without time zone not null
);

create table audit_events (
  tenant_id integer not null,
  event_name varchar(80) not null,
  actor_email varchar(255) null,
  message text not null,
  created_at timestamp without time zone not null
);
