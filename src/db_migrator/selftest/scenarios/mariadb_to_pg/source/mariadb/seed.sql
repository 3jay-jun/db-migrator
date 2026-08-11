set @large_rows = coalesce(@large_rows, 100000);
set @sequence_limit = greatest(@large_rows, 12000);

create temporary table seq (n int primary key);

insert into seq (n)
select d0.d + d1.d * 10 + d2.d * 100 + d3.d * 1000 + d4.d * 10000 + d5.d * 100000 + 1 as n
from
  (select 0 d union all select 1 union all select 2 union all select 3 union all select 4 union all select 5 union all select 6 union all select 7 union all select 8 union all select 9) d0
  cross join (select 0 d union all select 1 union all select 2 union all select 3 union all select 4 union all select 5 union all select 6 union all select 7 union all select 8 union all select 9) d1
  cross join (select 0 d union all select 1 union all select 2 union all select 3 union all select 4 union all select 5 union all select 6 union all select 7 union all select 8 union all select 9) d2
  cross join (select 0 d union all select 1 union all select 2 union all select 3 union all select 4 union all select 5 union all select 6 union all select 7 union all select 8 union all select 9) d3
  cross join (select 0 d union all select 1 union all select 2 union all select 3 union all select 4 union all select 5 union all select 6 union all select 7 union all select 8 union all select 9) d4
  cross join (select 0 d union all select 1 union all select 2 union all select 3 union all select 4 union all select 5 union all select 6 union all select 7 union all select 8 union all select 9) d5
where d0.d + d1.d * 10 + d2.d * 100 + d3.d * 1000 + d4.d * 10000 + d5.d * 100000 + 1 <= @sequence_limit;

insert into tenants (tenant_id, tenant_code, display_name, active, created_at) values
  (1, 'core', 'Core Services', true, timestamp('2026-01-01 09:00:00.123456')),
  (2, 'sleep', 'Sleep Care', true, timestamp('2026-01-02 09:00:00.123456')),
  (3, 'archive', 'Archived Tenant', false, timestamp('2026-01-03 09:00:00.123456'));

insert into roles (role_id, role_code, description) values
  (1, 'admin', 'Full access role'),
  (2, 'operator', 'Operational migration user'),
  (3, 'viewer', 'Read only user');

insert into app_users (
  user_id,
  tenant_id,
  email,
  full_name,
  birth_date,
  login_count,
  credit_limit,
  profile_json,
  user_uuid,
  created_at,
  updated_at
)
select
  n,
  ((n - 1) % 3) + 1,
  concat('user', n, '@example.test'),
  concat('Self Test User ', n),
  date_add(date('1980-01-01'), interval (n % 12000) day),
  n % 500,
  cast(1000 + (n % 10000) as decimal(14,2)),
  json_object(
    'tier', if(n % 5 = 0, 'enterprise', 'standard'),
    'flags', json_array('email', 'sms', 'push'),
    'score', n % 100
  ),
  concat('00000000-0000-4000-8000-', lpad(n, 12, '0')),
  timestampadd(second, n, timestamp('2026-02-01 00:00:00.123456')),
  timestampadd(second, n, timestamp('2026-02-01 00:00:00.123456'))
from seq
where n <= 5000;

insert into user_roles (user_id, role_id, granted_at)
select
  users.n,
  roles.n,
  timestampadd(minute, users.n + roles.n, timestamp('2026-03-01 00:00:00.123456'))
from seq users
join seq roles on roles.n <= 3
where users.n <= 5000
  and (users.n + roles.n) % 2 = 0;

insert into orders (
  order_id,
  user_id,
  order_no,
  status,
  total_amount,
  ordered_at,
  shipped_at
)
select
  n,
  ((n - 1) % 5000) + 1,
  concat('ORD-', lpad(n, 10, '0')),
  case
    when n % 11 = 0 then 'cancelled'
    when n % 5 = 0 then 'shipped'
    else 'paid'
  end,
  cast((n % 70000) / 3.0 as decimal(16,2)),
  timestampadd(second, n, timestamp('2026-04-01 00:00:00.123456')),
  case
    when n % 5 = 0 then timestampadd(second, n, timestamp('2026-04-02 00:00:00.123456'))
    else null
  end
from seq
where n <= 12000;

insert into order_items (
  order_id,
  line_no,
  sku,
  quantity,
  unit_price,
  discount_rate
)
select
  order_seq.n,
  line_seq.n,
  concat('SKU-', lpad((order_seq.n + line_seq.n) % 9000, 5, '0')),
  ((order_seq.n + line_seq.n) % 7) + 1,
  cast(((order_seq.n * line_seq.n) % 50000) / 10.0 as decimal(12,2)),
  cast(((order_seq.n + line_seq.n) % 100) / 1000.0 as decimal(5,4))
from seq order_seq
join seq line_seq on line_seq.n <= 3
where order_seq.n <= 12000;

insert into document_blobs (
  document_id,
  user_id,
  file_name,
  mime_type,
  content_text,
  content_bytes,
  metadata_json,
  created_at
)
select
  n,
  ((n - 1) % 5000) + 1,
  concat('document-', n, '.txt'),
  'text/plain',
  repeat(concat('large text payload ', n, ' '), 30),
  unhex(md5(n)),
  json_object('source', 'selftest', 'documentId', n, 'tags', json_array('blob', 'text')),
  timestampadd(minute, n, timestamp('2026-05-01 00:00:00.123456'))
from seq
where n <= 2000;

insert into bulk_events (
  event_id,
  tenant_id,
  event_type,
  payload_text,
  payload_json,
  occurred_at
)
select
  n,
  ((n - 1) % 3) + 1,
  if(n % 2 = 0, 'metric', 'activity'),
  repeat(concat('bulk event payload ', n, ' '), 12),
  json_object(
    'eventId', n,
    'tenant', ((n - 1) % 3) + 1,
    'nested', json_object('bucket', n % 100, 'ok', n % 2 = 0)
  ),
  timestampadd(second, n, timestamp('2026-06-01 00:00:00.123456'))
from seq
where n <= @large_rows;

insert into audit_events (
  tenant_id,
  event_name,
  actor_email,
  message,
  created_at
)
select
  ((n - 1) % 3) + 1,
  concat('audit-', lpad(n % 20, 2, '0')),
  concat('actor', lpad(n, 6, '0'), '@example.test'),
  concat('PK 없는 offset fallback 검증용 audit message ', lpad(n, 6, '0')),
  timestampadd(second, n, timestamp('2026-07-01 00:00:00.123456'))
from seq
where n <= 1000;

analyze table tenants, app_users, roles, user_roles, orders, order_items, document_blobs, bulk_events, audit_events;
