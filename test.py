import unittest
import json
import redis
from dateutil import parser
import datetime
import pytz

from disref.reference import Reference
from disref.process import Process
from disref.update import Update
from disref.cache import LruCache

class ProcessTest(unittest.TestCase):

    def test_init_establishes_connection_once(self):
        p1 = Process()
        p2 = Process()

        assert Process.client is p1.client
        assert Process.client is p2.client
        assert p1.client is p2.client

    def test_init_ignore_differing_connection_setting(self):
        p1 = Process()
        p2 = Process(host="notlocalhost", port=123)

        p2_connection = p2.client.connection_pool.connection_kwargs
        assert p2_connection['host'] != "notlocalhost"
        assert p2_connection['port'] != 123


class ReferenceTest(unittest.TestCase):

    def setUp(self):
        self.client = redis.StrictRedis(host='localhost')
        self.client.flushall()

    def tearDown(self):
        self.client = redis.StrictRedis(host='localhost')
        self.client.flushall()

    def test_init_creates_keys(self):
        p = Process()
        a = p.create_reference('foo')
        assert a.reflist_key == 'disref_foo.reflist'
        assert a.resource_key == 'foo'
        assert a.times_modified_key == 'disref_foo.times_modified'

    def test_lock_is_non_reentrant(self):
        p = Process()
        a = p.create_reference('foo')

        assert a.lock() == True
        assert a.lock(block=False) == False

    def test_lock_acquires_and_releases(self):
        p = Process()
        a = p.create_reference('foo')
        assert a.lock() == True
        assert a.lock(block=False) == False
        a.release()
        assert a.lock() == True

    def test_refresh_session_sets_time_initially(self):
        p = Process()
        a = p.create_reference('foo')
        a.refresh_session()
        reflist = json.loads(p.client.get(a.reflist_key) or "{}")
        assert len(reflist) == 1, "{0}: {1}".format(reflist, len(reflist))
        assert isinstance(parser.parse(reflist[p.id]), datetime.datetime)

    def test_refresh_session_resets_time(self):
        p = Process()
        a = p.create_reference('foo')
        reflist = json.loads(p.client.get(a.reflist_key) or "{}")
        start = parser.parse(reflist[p.id])
        a.refresh_session()
        reflist = json.loads(p.client.get(a.reflist_key) or "{}")
        end = parser.parse(reflist[p.id])
        assert end > start
        assert isinstance(end, datetime.datetime)
        assert isinstance(start, datetime.datetime)

    def test_get_and_increment_times_modified(self):
        p = Process()
        a = p.create_reference('foo')
        assert a.get_times_modified() == 0
        a.increment_times_modified()
        assert a.get_times_modified() == 1, a.get_times_modified()
        a.increment_times_modified()
        a.increment_times_modified()
        assert a.get_times_modified() == 3
        p2 = Process()
        b = p2.create_reference('foo')
        b.increment_times_modified()
        assert b.get_times_modified() == 4

    def test_count_for_one_reference(self):
        p = Process()
        a = p.create_reference('foo')
        assert a.count() == 1

    def test_count_for_multiple_references(self):
        p = Process()
        a = p.create_reference('foo')

        p2 = Process()
        b = p2.create_reference('foo')

        p3 = Process()
        c = p3.create_reference('foo')

        assert a.count() == b.count()
        assert b.count() == c.count()
        assert c.count() == 3

    def test_count_decrements_when_dereferenced(self):
        p = Process()
        a = p.create_reference('foo')

        p2 = Process()
        b = p2.create_reference('foo')

        p3 = Process()
        c = p3.create_reference('foo')

        assert a.count() == b.count()
        assert b.count() == c.count()
        assert c.count() == 3
        a.dereference()
        assert a.count() == 2
        b.dereference()
        assert a.count() == 1
        c.dereference()
        assert a.count() == 0, Reference.client.get(a.reflist_key)

    def test_remove_failed_processes(self):
        now = datetime.datetime.now(pytz.utc)
        expired = now - datetime.timedelta(seconds=2 * Process.TTL + 1)
        pids = {u'1': now.isoformat(),
                u'2': expired.isoformat()}

        p = Process()
        a = p.create_reference('biz')

        target = a.remove_failed_processes(pids)
        assert u'2' not in target, target
        assert u'1' in target, target
        assert target[u'1'] == now.isoformat(), target

    def test_dereference_removes_pid_from_pids(self):
        p = Process()
        a = p.create_reference('foo')

        p2 = Process()
        b = p2.create_reference('foo')

        pids = json.loads(Process.client.get(a.reflist_key) or "{}")
        assert a._Reference__process.id in pids
        assert b._Reference__process.id in pids
        a.dereference()
        pids = json.loads(a._Reference__process.client.get(a.reflist_key) or "{}")
        assert a._Reference__process.id not in pids
        b.dereference()
        pids = json.loads(b._Reference__process.client.get(b.reflist_key) or "{}")
        assert b._Reference__process.id not in pids
        assert len(pids) == 0

    def test_dereference_cleans_up(self):
        p = Process()
        a = p.create_reference('foo')

        p2 = Process()
        b = p2.create_reference('foo')

        pids = json.loads(Process.client.get(a.reflist_key) or "{}")
        assert a._Reference__process.id in pids
        assert b._Reference__process.id in pids
        a.dereference()
        pids = json.loads(a._Reference__process.client.get(a.reflist_key) or "{}")
        assert a._Reference__process.id not in pids
        b.dereference()
        pids = json.loads(b._Reference__process.client.get(b.reflist_key) or "{}")
        assert b._Reference__process.id not in pids
        assert len(pids) == 0
        assert Process.client.get(a.reflist_key) == None, Process.client.get(a.reflist_key) 
        assert Process.client.get(a.resource_key) == None, Process.client.get(a.resource_key)
        assert Process.client.get(a.times_modified_key) == None, Process.client.get(a.times_modified_key)

    def test_dereference_handles_when_never_modified(self):
        p = Process()
        a = p.create_reference('foo')
        pids = json.loads(a._Reference__process.client.get(a.reflist_key) or "{}")
        assert len(pids) == 1, pids

        a.dereference()
        pids = json.loads(a._Reference__process.client.get(a.reflist_key) or "{}")
        assert len(pids) == 0, pids

    def test_dereference_calls_callback(self):
        p = Process()
        a = p.create_reference('foo')

        p2 = Process()
        b = p2.create_reference('foo')
        foo = [1]
        
        def callback(*args, **kwargs):
            foo.pop()

        b.dereference(callback, args=('first',))
        assert len(foo) == 1
        a.dereference(callback, args=('second',))
        assert len(foo) == 0

class UpdateTest(unittest.TestCase):

    class UserUpdate(Update):

        def merge(self, user_update):
            for k, v in user_update.get('doc', {}).items():
                if k not in self.doc:
                    self.doc[k] = float(v)
                else:
                    self.doc[k] += float(v)

        def cache(self):
            obj = {
                    'doc': self.doc,
                    'spec': self.spec,
                    'collection': self.collection,
                    'database': self.database
            }
            client = self._Update__process.client
            client.set(self.resource_id, json.dumps(obj))

        def execute(self):
            obj = {
                    'doc': self.doc,
                    'spec': self.spec,
                    'collection': self.collection,
                    'database': self.database
            }
            client = self._Update__process.client
            client.set("{0}.write".format(self.resource_id), json.dumps(obj))

    def test_initializer_updates_ref_count(self):
        a = UpdateTest.UserUpdate(process=Process(), _id='123', database='test', collection='user',
                spec={'_id': 123}, doc={'a': 1., 'b': 2., 'c': 3.})

        client = a._Update__process.client
        reflist = json.loads(client.get(a.ref.reflist_key) or "{}")
        assert len(reflist) == 1
        assert a._Update__process.id in reflist

    def test_cache_caches(self):
        p = Process()
        a = UpdateTest.UserUpdate(process=p, _id='12345', database='test', collection='user',
                spec={'_id': 12345}, doc={'a': 1., 'b': 2., 'c': 3.})
        a.cache()
        client = a._Update__process.client
        cached = json.loads(client.get(a.resource_id) or "{}")
        assert cached == {u'doc': {u'a': 1.0, u'c': 3.0, u'b': 2.0},
                u'spec': {u'_id': 12345},
                u'collection': u'user',
                u'database': u'test'}

        client.flushall()
        b = UpdateTest.UserUpdate(process=p, _id='456', database='test', collection='user',
                spec= {u'_id': 456}, doc={'d': 4., 'e': 5., 'f': 6.})
        p2 = Process()
        c = UpdateTest.UserUpdate(process=p2, _id='456', database='test', collection='user',
                spec= {u'_id': 456}, doc={'d': 4., 'e': 5., 'f': 6.})

        client = a._Update__process.client
        assert client.get(b.resource_id) is None, client.get(b.resource_id)

        assert c.ref.count() == 2, c.ref.count()
        b.end_session()
        assert c.ref.count() == 1, c.ref.count()
        cached = json.loads(client.get(b.resource_id) or "{}") 

        observed_doc = cached['doc']
        observed_spec = cached['spec']
        observed_coll = cached['collection']
        observed_db = cached['database']
        
        expected_doc = {u'd': 4.0, u'e': 5.0, u'f': 6.0}
        expected_spec = {u'_id': 456}
        expected_coll = u'user'
        expected_db = u'test'

        for k, v in observed_doc.items():
            assert expected_doc[k] == v, k
        for k, v in expected_doc.items():
            assert observed_doc[k] == v, k

        assert c.ref.count() == 1, c.ref.count()

        assert observed_coll == expected_coll
        assert observed_db == expected_db

        c.end_session()
        assert c.ref.count() == 0, c.ref.count()
        assert client.get(c.resource_id) is None, client.get(c.resource_id)

        target = json.loads(client.get("{0}.write".format(b.resource_id)) or "{}")

        expected_doc = {u'd': 8.0, u'e': 10.0, u'f': 12.0}
        expected_spec = {u'_id': 456}
        expected_coll = u'user'
        expected_db = u'test'
       
        for k, v in target.get('doc').items():
            assert expected_doc[k] == v
        for k, v in expected_doc.items():
            assert target['doc'][k] == v

    def test_end_session_raises_when_deadlocked(self):
        pass

    def test_end_session_executes_for_unique_references(self):
        pass

class LruCacheTest(unittest.TestCase):

    def setUp(self):
        self.cache = LruCache(max_entries=5)

    def get_update(self, key):
        class Update(object):
            def __init__(self, key):
                self.key = key
                self.__called = False
            def merge(self, other):
                self.__other = other
            def end_session(self):
                self.__called = True 
            def assert_end_session_called(self):
                assert self.__called
            def assert_merged(self, other):
                assert other is self.__other
        return Update(key)

    def test_set_reorders_repeated_elements(self):
        a = self.get_update('a')
        b = self.get_update('b')
        c = self.get_update('c')
        self.cache.set(1, a)
        assert self.cache.size() == 1
        self.cache.set(2, b)
        assert self.cache.size() == 2
        self.cache.set(1, a)
        assert self.cache.size() == 2
        a.assert_merged(a)
        self.cache.expire_oldest()
        b.assert_end_session_called()
        assert self.cache.size() == 1

    def test_set_expires_oldest_to_add_new(self):
        a = self.get_update('a')
        b = self.get_update('b')
        c = self.get_update('c')
        d = self.get_update('d')
        e = self.get_update('e')
        f = self.get_update('f')

        assert self.cache.size() == 0
        self.cache.set('a', a)
        assert self.cache.size() == 1
        self.cache.set('b', b)
        assert self.cache.size() == 2
        self.cache.set('c', c)
        assert self.cache.size() == 3
        self.cache.set('d', d)
        assert self.cache.size() == 4
        self.cache.set('e', e)
        assert self.cache.size() == 5
        self.cache.set('f', f)
        assert self.cache.size() == 5

        a.assert_end_session_called()

    def test_get_returns_elements(self):
        a = self.get_update('a')
        self.cache.set('a', a) 
        assert self.cache.get('a') is a
        assert self.cache.size() == 1
        assert self.cache.get('a') is a

    def test_expire_expires_at_key(self):
        a = self.get_update('a')
        b = self.get_update('b')

        self.cache.set('a', a)
        self.cache.set('b', b)
        assert self.cache.size() == 2

        assert self.cache.get('a') is a
        self.cache.expire('a')
        assert self.cache.size() == 1
        a.assert_end_session_called()


    def test_expire_all_expires_all(self):
        updates = [self.get_update('a'),
                   self.get_update('b'),
                   self.get_update('c'),
                   self.get_update('d'),
                   self.get_update('e')]
       
        for size, update in enumerate(updates):
            self.cache.set(update.key, update)
            assert self.cache.size() == size + 1

        self.cache.expire_all()
        assert self.cache.size() == 0
        for update in updates:
            update.assert_end_session_called()

    def test_failres_are_kept(self):
        class FailingUpdate(object):
            def end_session(self):
                raise Exception("Failed.")

        failing = FailingUpdate()
        self.cache.set('a', failing)
        try:
            self.cache.expire('a')
        except Exception, e:
            pass

        assert self.cache.get_last_failed() is failing
