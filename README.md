# Installation

You can install this package pretty easily with setup.py

```
python setup.py install
```

Or you can use git+ssh:

```
pip install git+ssh://git@github.com/buzzfeed/disref.git 
```

But you can also use pip if you clone...

```
git clone git@github.com:buzzfeed/disref.git ./disref; cd disref; pip install .
```

# Run the tests

This package uses the standard `setup.py` approach:

```
python setup.py test
```

# Getting Started

## References

The building blocks for this approach to concurrency is the `Reference` object. You can use `Reference` s for 

* locking on resources for exclusive reads or writes
* finding out how many processes are using a resource at a given time
* keeping track of how many processes have modified that resource
* executing a callback when a process is the last to finish using a resource

Here's an example:

```python
from disref.reference import Reference
from disref.process import Process

p1 = Process()
address_lookup_service = p1.create_reference(resource='me')
p2 = Process()
email_verification_service = p2.create_reference(resource='me')

def lookup_email_and_apply_to_record(record, reference):
    email = get_email(record)
    try:
        reference.lock()
        update_record_with_email(record, email)
        if reference.count() == 1:
            write_record_to_database(record)
        reference.release()
    except disref.AlreadyLocked, e:
        raise Exception("Timed out acquiring lock!")

def verify_address_and_apply_to_record(record, reference):
    address = get_address(record)
    try:
        reference.lock()
        update_record_with_address(record, address)
        if reference.count() == 1:
            write_record_to_database(record)
        reference.release()
    except disref.AlreadyLocked, e:
        raise Exception("Timed out acquiring lock!")

t1 = threading.Thread(target=lookup_email_and_apply_to_record,
    args=('me', email_verification_service))
t2 = threading.Thread(target=verify_address_and_apply_to_record,
    args=('me', address_lookup_service))
t1.start()
t2.start()
t1.join()
t2.join()
```

Whoever is last to update the record in the cache will know since `count()` will return `1`. At that point we'll know the record is finished being updated, and is ready to be written to the database. 

## Updates

You can see the above example is pretty redundant in this case. It's much more useful to make use of a passive design, subscribing to incoming messages, and using sessions to decide when to write. That is what the `Update` class is intended to do. I'll just write a for loop to simulate incoming messages.

```python
from disref.update import Update
from disref.cache import LruCache

class UserUpdate(Update):
    def cache(self):
        # Cache this object to redis. Don't worry about locking, etc, it's handled.
    def merge(self, other):
        # Merge an instance of this class with another, combining state.
    def execute(self):
        # Write this object to the database. don't worry about when to cache vs. execute, it's handled.

# Calls end_session upon removing from cache.
# Also finds collisions and calls merge instead of overwriting on set
lru_cache = LruCache(max_entries=10000) 

p = Process()
for user_update in user_updates:
    lru_cache.set(user_update.user_id, Update(process=p, doc=**user_update))

lru_cache.expire_all()
```
