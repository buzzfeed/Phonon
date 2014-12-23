import sherlock
import redis
import uuid
import datetime
import threading
import math

from dateutil import parser

from disref import get_logger, DISREF_NAMESPACE
from disref.reference import Reference

logger = get_logger(__name__)

class Process(object):
    """
    Represents a process on which a particular resource lives, identified by a
    unique id automatically assigned to it.  It establishes a connection to
    Redis, shared by all instances. All References should be added through a
    process instance.

    When finished with the process instance, call the stop() function.

    """

    TTL = 30 * 60  # 30 minutes
    RETRY_SLEEP = 0.5    # Second
    TIMEOUT = 500

    def __init__(self, session_length=int(0.5*TTL), host='localhost', port=6379, db=1, heartbeat_interval=10):
        """
        :param session_length int: The session length for the resource. e.g. If
            this represents an update for a User, the session_length would be
            the session length for that user. This should be at most 1/2 the
            length of the TTL for the Reference.
        :param str host: The host to connect to redis over.
        :param int port: The port to connect to redis on.
        :param int heartbeat_interval: The frequency in seconds with which to
            update the heartbeat for this process.


        """
        self.id = unicode(uuid.uuid4())
        self.session_length = session_length

        if not hasattr(Process, 'client'):
            Process.client = redis.StrictRedis(host=host, port=port, db=db)
            sherlock.configure(backend=sherlock.backends.REDIS,
                               expire=self.TTL,
                               retry_interval=self.RETRY_SLEEP,
                               timeout=self.TIMEOUT)
        else:
            connection_kwargs = Process.client.connection_pool.connection_kwargs
            if connection_kwargs['port'] != port or connection_kwargs['host'] != host or connection_kwargs['db'] != db:
                logger.warning("An existing Redis connection exists: host {0}, port {1}, db {2}.  Your connection paramters\
                                are being ignored."
                                .format(connection_kwargs['port'], connection_kwargs['host'], connection_kwargs['db']))

        self.client = Process.client

        self.registry_key = self._get_registry_key(self.id)

        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_hash_name = "{0}_heartbeat".format(DISREF_NAMESPACE)
        self.__heartbeat_ref = self.create_reference(self.heartbeat_hash_name)
        self.__update_heartbeat()

    def create_reference(self, resource, block=True):
        """
        Creates a Reference object owned by this process.

        :param bool block: Optional. Whether or not to block when establishing
            locks.
        :param str resource: An identifier for the resource. For example:
            Buzz.12345

        :returns: The created Reference object
        """

        self.add_to_registry(resource)
        return Reference(self, resource, block)

    def add_to_registry(resource, registry_key=None):
        if registry_key is None:
            registry_key = self.registry_key

        self.client.hset(self.registry_key, resource, 1)

    def remove_from_registry(resource, registry_key=None):
        if registry_key is None:
            registry_key = self.registry_key

        self.client.hdel(self.registry_key, resource)


    def _get_registry_key(pid):
        return "{0}_{1}".format(DISREF_NAMESPACE, pid)

    def __update_heartbeat(self):
        """
        Records the timestamp at a configurable interval to ensure the process is still alive.
        """
        self.__heartbeat_timer = None
        self.__heartbeat_ref.lock()

        try:
            self.client.hset(self.heartbeat_hash_name, self.id, datetime.datetime.now())
        finally:
            self.__heartbeat_ref.release()

        self.__heartbeat_timer = threading.Timer(self.heartbeat_interval, self.__update_heartbeat)
        self.__heartbeat_timer.start()

    def __check_heartbeats(self):
        """
        Ensures that all other processes are still alive.
        """
        failed_pids = []
        heartbeats = self.client.hgetall(self.heartbeat_hash_name)
        for pid, time in heartbeats.item():
            if parser.parse(time) <= datetime.datetime.now() - datetime.timedelta(seconds=(5*self.heartbeat_interval)):
                failed_pids.append(pid)

        active_process_count = len(heartbeats) - len(failed_pids)
        for failed_pid in failed_pids:
            failed_process_registry_key = self._get_registry_key(failed_pid)
            failed_process_registry_ref = self.create_reference(failed_registry_key)

            try:
                failed_registry_ref.lock()
                failed_process_registry = self.client.hkeys(failed_process_registry_key)

                if failed_pid == self.pid:
                    recovering_references = failed_process_registry_ref
                    self.id = unicode(uuid.uuid4())
                    self.registry_key = self._get_registry_key(self.id)
                else:
                    recovering_references = failed_process_registry[0:int(math.ceil(float(len(failed_process_registry))/active_process_count))]

                for recovering_reference in recovering_references:
                    self.create_reference(recovering_reference)

                if self.client.hdel(failed_process_registry_key, recovering_references) == 0:
                    # No futher references to recovery.
                    self.client.hdel(self.heartbeat_hash_name, failed_pid)
            except Reference.AlreadyLocked:
                logger.error("Reference already locked")
            finally:
                failed_registry_ref.dereference()
                failed_registry_ref.release()




    def stop(self):
        """
        Preforms cleanup for the Process instance when it is to be terminated.
        """
        if self.__heartbeat_timer:
            self.__heartbeat_timer.cancel()

        if self.__heartbeat_ref.lock(self.__heartbeat_ref.block):
            self.__heartbeat_ref.dereference()
        self.__heartbeat_ref.release()

    def __del__(self):
        self.stop()
