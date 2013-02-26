import sys, urllib2
from django.conf import settings
from django.db import models
from django.core.files.storage import default_storage

import logging
logger = logging.getLogger(__name__)

class Location(models.Model):
    '''Class to store metadata about a storage location

    :attribute name: the name for the location
    :attribute url: the url for the location
    :attribute type: one of 'online', 'offline' and 'external'
    :attribute priority: a priority score that is used to rank the locations
        when deciding which one to use
    :attribute is_available: if True, the location should currently be
        accessible / useable

    ... and other attributes TBD
    '''

    name = models.CharField(max_length=10, unique=True)
    url = models.CharField(max_length=400, unique=True)
    type = models.CharField(max_length=10)
    priority = models.IntegerField()
    is_available = models.BooleanField(default=True)
    trust_length = models.BooleanField(default=False)
    metadata_supported = models.BooleanField(default=False)
    auth_user = models.CharField(max_length=20, blank=True)
    auth_password = models.CharField(max_length=400, blank=True)
    auth_realm = models.CharField(max_length=20, blank=True)
    auth_scheme = models.CharField(max_length=10, default='digest')
    migration_provider = models.CharField(max_length=10, default='local')

    initialized = False

    class Meta:
        app_label = 'tardis_portal'

    def __init__(self, *args, **kwargs):
        super(Location, self).__init__(*args, **kwargs)
        self._provider = None

    def _get_provider(self):
        if not self._provider:
            self._provider = Location.build_provider(self)
        return self._provider

    provider = property(_get_provider)

    def get_priority(self):
        '''Return the location's priority, or -1 if it is not available'''
        if self.is_available:
            return self.priority
        else:
            return -1

    @classmethod
    def get_default_location(cls):
        '''Lookup the default location'''
        return Location.get_location(settings.DEFAULT_LOCATION)

    @classmethod
    def get_location(cls, loc_name):
        '''Lookup a named location'''

        try:
            return Location.objects.get(name=loc_name)
        except Location.DoesNotExist:
            if not cls._check_initialized():
                return cls.get_location(loc_name)
            else:
                return None

    @classmethod
    def get_location_for_url(cls, url):
        '''Reverse lookup a location from a url'''
        
        for location in Location.objects.all():
            if url.startswith(location.url):
                return location
        if not cls._check_initialized():
            return cls.get_location_for_url(url)
        else:
            return None

    @classmethod
    def _check_initialized(cls):
        '''Attempt to initialize if we need to''' 
        if cls.initialized:
            return True
        res = cls.force_initialize()
        cls.initialized = True
        return res

    @classmethod
    def force_initialize(cls):
        done_init = False
        for desc in settings.INITIAL_LOCATIONS:
            try:
                logger.debug('Checking location %s' % desc['name'])
                Location.objects.get(name=desc['name'])
                logger.debug('Location %s already exists' % desc['name'])
            except Location.DoesNotExist:
                url = desc['url']
                if not url.endswith('/'):
                    url = url + '/'
                location = Location(
                    name=desc['name'],
                    url=url,
                    type=desc['type'],
                    priority=desc['priority'],
                    migration_provider=desc.get('provider', 'local'),
                    trust_length=desc.get('trust_length', False),
                    metadata_supported=desc.get('metadata_supported', False),
                    auth_user=desc.get('user', ''),
                    auth_password=desc.get('password', ''),
                    auth_realm=desc.get('realm', ''),
                    auth_scheme=desc.get('scheme', 'digest'))
                location.save()
                logger.info('Location %s created' % desc['name'])
                done_init = True
        return done_init

    @classmethod
    def get_provider(cls, location_id):
        loc = Location.objects.get(id=location_id)
        return cls.build_provider(loc)

    @classmethod
    def build_provider(cls, loc):
        if loc.auth_user:
            password_mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(loc.auth_realm, loc.url, 
                                      loc.auth_user, loc.auth_password)
            if loc.auth_scheme == 'basic':
                handler = urllib2.HTTPBasicAuthHandler(password_mgr)
            elif loc.auth_scheme == 'digest':
                handler = urllib2.HTTPDigestAuthHandler(password_mgr)
            else:
                raise ValueError('Unknown auth type "%s"' % loc.auth_scheme)
            opener = urllib2.build_opener(handler)
        else:
            opener = urllib2.build_opener()

        # FIXME - is there a better way to do this?
        exec 'import tardis\n' + \
            'provider = ' + \
            settings.MIGRATION_PROVIDERS[loc.migration_provider] + \
                '(loc.name, loc.url, opener, ' + \
                'metadata_supported=loc.metadata_supported)'
        return provider

    def __unicode__(self):
        return self.name

