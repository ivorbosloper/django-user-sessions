import logging
from django.contrib.auth import SESSION_KEY

from django.contrib.sessions.backends.base import SessionBase, CreateError
from django.core.exceptions import SuspiciousOperation
from django.db import IntegrityError, transaction, router
from django.utils import timezone
from django.utils.encoding import force_text


class SessionStore(SessionBase):
    """
    Implements database session store.
    """
    def __init__(self, user_agent, ip, session_key=None):
        super(SessionStore, self).__init__(session_key)
        self.user_agent, self.ip, self.user_id = user_agent[:200], ip, None

    def __setitem__(self, key, value):
        if key == SESSION_KEY:
            self.user_id = value
        super(SessionStore, self).__setitem__(key, value)

    def load(self):
        try:
            s = Session.objects.get(
                session_key=self.session_key,
                expire_date__gt=timezone.now()
            )
            self.user_id = s.user_id
            # do not overwrite user_agent/ip, as those might have been updated
            if self.user_agent != s.user_agent or self.ip != s.ip:
                self.modified = True
            return self.decode(s.session_data)
        except (Session.DoesNotExist, SuspiciousOperation) as e:
            if isinstance(e, SuspiciousOperation):
                logger = logging.getLogger('django.security.%s' %
                                           e.__class__.__name__)
                logger.warning(force_text(e))
            self.create()
            return {}

    def exists(self, session_key):
        return Session.objects.filter(session_key=session_key).exists()

    def create(self):
        while True:
            self._session_key = self._get_new_session_key()
            try:
                # Save immediately to ensure we have a unique entry in the
                # database.
                self.save(must_create=True)
            except CreateError:
                # Key wasn't unique. Try again.
                continue
            self.modified = True
            self._session_cache = {}
            return

    def save(self, must_create=False):
        """
        Saves the current session data to the database. If 'must_create' is
        True, a database error will be raised if the saving operation doesn't
        create a *new* entry (as opposed to possibly updating an existing
        entry).
        """
        obj = Session(
            session_key=self._get_or_create_session_key(),
            session_data=self.encode(self._get_session(no_load=must_create)),
            expire_date=self.get_expiry_date(),
            user_agent=self.user_agent,
            user_id=self.user_id,
            ip=self.ip,
        )
        using = router.db_for_write(Session, instance=obj)
        try:
            with transaction.atomic(using=using):
                obj.save(force_insert=must_create, using=using)
        except IntegrityError as e:
            if must_create and 'session_key' in str(e):
                raise CreateError
            raise

    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        try:
            Session.objects.get(session_key=session_key).delete()
        except Session.DoesNotExist:
            pass

    @classmethod
    def clear_expired(cls):
        Session.objects.filter(expire_date__lt=timezone.now()).delete()


# At bottom to avoid circular import
from ..models import Session
