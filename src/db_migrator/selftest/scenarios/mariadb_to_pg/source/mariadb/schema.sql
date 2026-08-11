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
  tenant_id int primary key,
  tenant_code varchar(32) not null unique,
  display_name varchar(120) not null,
  active boolean not null,
  created_at datetime(6) not null
);

create table app_users (
  user_id bigint primary key,
  tenant_id int not null,
  email varchar(255) not null unique,
  full_name varchar(120) not null,
  birth_date date null,
  login_count int unsigned not null,
  credit_limit decimal(14,2) not null,
  profile_json json null,
  user_uuid char(36) not null,
  created_at datetime(6) not null,
  updated_at timestamp(6) not null,
  constraint fk_app_users_tenants foreign key (tenant_id) references tenants(tenant_id)
);

create table roles (
  role_id int primary key,
  role_code varchar(50) not null unique,
  description text null
);

create table user_roles (
  user_id bigint not null,
  role_id int not null,
  granted_at datetime(6) not null,
  primary key (user_id, role_id),
  constraint fk_user_roles_users foreign key (user_id) references app_users(user_id),
  constraint fk_user_roles_roles foreign key (role_id) references roles(role_id)
);

create table orders (
  order_id bigint primary key,
  user_id bigint not null,
  order_no varchar(40) not null unique,
  status enum('paid', 'shipped', 'cancelled') not null,
  total_amount decimal(16,2) not null,
  ordered_at timestamp(6) not null,
  shipped_at datetime(6) null,
  constraint fk_orders_users foreign key (user_id) references app_users(user_id)
);

create table order_items (
  order_id bigint not null,
  line_no int not null,
  sku varchar(80) not null,
  quantity int not null,
  unit_price decimal(12,2) not null,
  discount_rate decimal(5,4) not null,
  primary key (order_id, line_no),
  constraint fk_order_items_orders foreign key (order_id) references orders(order_id)
);

create table document_blobs (
  document_id bigint primary key,
  user_id bigint not null,
  file_name varchar(180) not null,
  mime_type varchar(100) not null,
  content_text longtext null,
  content_bytes longblob null,
  metadata_json json null,
  created_at datetime(6) not null,
  constraint fk_document_blobs_users foreign key (user_id) references app_users(user_id)
);

create table bulk_events (
  event_id bigint primary key,
  tenant_id int not null,
  event_type varchar(40) not null,
  payload_text longtext not null,
  payload_json json null,
  occurred_at datetime(6) not null,
  constraint fk_bulk_events_tenants foreign key (tenant_id) references tenants(tenant_id)
);

create table audit_events (
  tenant_id int not null,
  event_name varchar(80) not null,
  actor_email varchar(255) null,
  message text not null,
  created_at datetime(6) not null
);
