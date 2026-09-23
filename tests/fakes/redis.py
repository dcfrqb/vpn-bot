"""Минимальный in-memory async Redis для тестов.

set NX/EX, get, delete, exists, incr, expire, setex, sets (sadd/srem/smembers),
ping и eval двух Lua-скриптов бота: compare-and-delete (снятие лока) и
compare-and-expire (продление LeaderLock). TTL запоминаются в self.ttl, но не
истекают сами: тесты двигают время явно (expire_now).
"""


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttl = {}
        self.down = False  # True -> каждый вызов бросает ConnectionError

    def _check(self):
        if self.down:
            raise ConnectionError("fake redis down")

    async def ping(self):
        self._check()
        return True

    async def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.ttl[key] = ex
        return True

    async def get(self, key):
        self._check()
        return self.store.get(key)

    async def delete(self, *keys):
        self._check()
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                self.ttl.pop(k, None)
                n += 1
        return n

    async def exists(self, key):
        self._check()
        return 1 if key in self.store else 0

    async def incr(self, key):
        self._check()
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self._check()
        if key in self.store:
            self.ttl[key] = int(ttl)
            return True
        return False

    async def setex(self, key, ttl, value):
        self._check()
        self.store[key] = value
        self.ttl[key] = ttl
        return True

    async def sadd(self, key, *members):
        self._check()
        s = self.store.setdefault(key, set())
        before = len(s)
        s.update(members)
        return len(s) - before

    async def srem(self, key, *members):
        self._check()
        s = self.store.get(key, set())
        n = len([m for m in members if m in s])
        s.difference_update(members)
        return n

    async def smembers(self, key):
        self._check()
        return set(self.store.get(key, set()))

    async def eval(self, script, numkeys, *args):
        self._check()
        key, token = args[0], args[1]
        if self.store.get(key) != token:
            return 0
        if "'expire'" in script:
            self.ttl[key] = int(args[2])
            return 1
        del self.store[key]
        self.ttl.pop(key, None)
        return 1

    def expire_now(self, key):
        """Тест: ключ «истек»."""
        self.store.pop(key, None)
        self.ttl.pop(key, None)
