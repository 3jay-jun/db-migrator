create table users (
  id integer primary key,
  email varchar(255) not null
);

create table orders (
  id integer primary key,
  user_id integer not null references users(id),
  total_amount numeric(12,2) not null
);

insert into users (id, email) values (1, 'user@example.com');
insert into orders (id, user_id, total_amount) values (1, 1, 10.00);
