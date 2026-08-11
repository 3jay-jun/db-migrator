\set large_rows :large_rows

insert into tenants (tenant_id, tenant_code, display_name, active, created_at) values
  (1, 'core', 'Core Services', true, timestamp '2026-01-01 09:00:00'),
  (2, 'sleep', 'Sleep Care', true, timestamp '2026-01-02 09:00:00'),
  (3, 'archive', 'Archived Tenant', false, timestamp '2026-01-03 09:00:00');

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
  generated_id,
  ((generated_id - 1) % 3) + 1,
  'user' || generated_id || '@example.test',
  'Self Test User ' || generated_id,
  date '1980-01-01' + ((generated_id % 12000)::int),
  (generated_id % 500)::int,
  (1000 + (generated_id % 10000))::numeric(14,2),
  jsonb_build_object(
    'tier', case when generated_id % 5 = 0 then 'enterprise' else 'standard' end,
    'flags', jsonb_build_array('email', 'sms', 'push'),
    'score', generated_id % 100
  ),
  ('00000000-0000-4000-8000-' || lpad(generated_id::text, 12, '0'))::uuid,
  timestamp '2026-02-01 00:00:00' + (generated_id || ' seconds')::interval,
  timestamptz '2026-02-01 00:00:00+00' + (generated_id || ' seconds')::interval
from generate_series(1, 5000) as generated_id;

insert into user_roles (user_id, role_id, granted_at)
select
  user_id,
  role_id,
  timestamp '2026-03-01 00:00:00' + ((user_id + role_id) || ' minutes')::interval
from generate_series(1, 5000) as user_id
cross join generate_series(1, 3) as role_id
where (user_id + role_id) % 2 = 0;

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
  generated_id,
  ((generated_id - 1) % 5000) + 1,
  'ORD-' || lpad(generated_id::text, 10, '0'),
  case
    when generated_id % 11 = 0 then 'cancelled'
    when generated_id % 5 = 0 then 'shipped'
    else 'paid'
  end,
  ((generated_id % 70000) / 3.0)::numeric(16,2),
  timestamptz '2026-04-01 00:00:00+00' + (generated_id || ' seconds')::interval,
  case
    when generated_id % 5 = 0 then timestamp '2026-04-02 00:00:00' + (generated_id || ' seconds')::interval
    else null
  end
from generate_series(1, 12000) as generated_id;

insert into order_items (
  order_id,
  line_no,
  sku,
  quantity,
  unit_price,
  discount_rate
)
select
  order_id,
  line_no,
  'SKU-' || lpad(((order_id + line_no) % 9000)::text, 5, '0'),
  ((order_id + line_no) % 7) + 1,
  (((order_id * line_no) % 50000) / 10.0)::numeric(12,2),
  (((order_id + line_no) % 100) / 1000.0)::numeric(5,4)
from generate_series(1, 12000) as order_id
cross join generate_series(1, 3) as line_no;

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
  generated_id,
  ((generated_id - 1) % 5000) + 1,
  'document-' || generated_id || '.txt',
  'text/plain',
  repeat('large text payload ' || generated_id || ' ', 30),
  decode(md5(generated_id::text), 'hex'),
  jsonb_build_object('source', 'selftest', 'documentId', generated_id, 'tags', jsonb_build_array('blob', 'text')),
  timestamp '2026-05-01 00:00:00' + (generated_id || ' minutes')::interval
from generate_series(1, 2000) as generated_id;

insert into bulk_events (
  event_id,
  tenant_id,
  event_type,
  payload_text,
  payload_json,
  occurred_at
)
select
  generated_id,
  ((generated_id - 1) % 3) + 1,
  case when generated_id % 2 = 0 then 'metric' else 'activity' end,
  repeat('bulk event payload ' || generated_id || ' ', 12),
  jsonb_build_object(
    'eventId', generated_id,
    'tenant', ((generated_id - 1) % 3) + 1,
    'nested', jsonb_build_object('bucket', generated_id % 100, 'ok', generated_id % 2 = 0)
  ),
  timestamp '2026-06-01 00:00:00' + (generated_id || ' seconds')::interval
from generate_series(1, :large_rows) as generated_id;

insert into audit_events (
  tenant_id,
  event_name,
  actor_email,
  message,
  created_at
)
select
  ((generated_id - 1) % 3) + 1,
  'audit-' || lpad((generated_id % 20)::text, 2, '0'),
  'actor' || lpad(generated_id::text, 6, '0') || '@example.test',
  'PK 없는 offset fallback 검증용 audit message ' || lpad(generated_id::text, 6, '0'),
  timestamp '2026-07-01 00:00:00' + (generated_id || ' seconds')::interval
from generate_series(1, 1000) as generated_id;

analyze;
