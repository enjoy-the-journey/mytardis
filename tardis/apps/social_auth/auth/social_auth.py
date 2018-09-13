import logging

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.core.mail import get_connection
from django.contrib.auth.models import User, Permission
from django.contrib import messages
from django.urls import reverse

from celery.task import task

from tardis.tardis_portal.models import UserAuthentication

from tardis.apps.openid_migration.models import OpenidUserMigration
from tardis.apps.social_auth import default_settings as social_auth_default_settings
from tardis.apps.openid_migration import default_settings as openid_migration_default_settings
from tardis import default_settings

logger = logging.getLogger(__name__)


def add_authentication_method(**kwargs):
    """Creates an authentication record for OpenID authenticated user"""
    # add authentication method only if is a new user
    isNewUser = kwargs.get('is_new')
    if not isNewUser:
        return None

    backend = kwargs.get('backend')
    authenticatedBackendName = type(backend).__name__
    user = kwargs.get('user')
    # get auth method from backend
    authMethod = get_auth_method(authenticatedBackendName)

    try:
        authentication = UserAuthentication(userProfile=user.userprofile,
                                            username=user.username,
                                            authenticationMethod=authMethod,
                                            approved=False)
        authentication.save()
        kwargs['authentication'] = authentication
    except:
        pass
    return kwargs


def get_auth_method(authenticatedBackendName):
    """
    Return matching user authentication method from list of authentication
    methods in settings
    """

    for authKey, authDisplayName, authBackend in settings.AUTH_PROVIDERS:
        authBackendClassName = authBackend.split('.')[-1]
        if authBackendClassName == authenticatedBackendName:
            return authKey
    return None


def add_user_permissions(**kwargs):
    """
    Adds default permission to OpenID authenticated user
    """
    user = kwargs.get('user')
    if user:
        for perm in ['add_experiment', 'change_experiment', 'change_group',
                     'change_objectacl',
                     'add_datafile', 'change_dataset']:
            user.user_permissions.add(Permission.objects.get(codename=perm))

    return kwargs


def add_migration_permission(**kwargs):
    """
    Adds permission to migrate account for OpenID authenticated user
    """
    user = kwargs.get('user')
    # Check if migration has been performed and add permission only if it is false
    is_account_migrated = OpenidUserMigration.objects.filter(new_user=user)
    if user and not is_account_migrated:
        user.user_permissions.add(Permission.objects.get(codename='add_openidusermigration'))

    return kwargs


def approve_user_auth(**kwargs):
    """
    Sets approved status to True in user authentication
    This will add user permissions as well.
    """
    '''
    :param kwargs:
    :return: kwargs
    '''

    isNewUser = kwargs.get('is_new')
    if not isNewUser:
        return None

    authentication = kwargs.get('authentication')
    authentication.approved = True
    authentication.save()
    return kwargs


def send_admin_email(**kwargs):
    """
    Sends MyTardis admins an email for approving account
    """

    isNewUser = kwargs.get('is_new')
    if not isNewUser:
        return None

    # get user
    user = kwargs.get('user')
    if user:
        authentication = kwargs.get('authentication')
        # send email to admins
        site = Site.objects.get_current().domain
        subject = '[MyTardis] User account needs admin approval'
        message = (
            "Hi, This message is for MyTardis Admins.\n\n"
            "A MyTardis user account with username as \"%s\" and user_id as "
            "\"%s\" was recently created and needs admin approval.\n\n"
            "%s/admin/tardis_portal/userauthentication/%s\n\n"
            "Thanks,\n"
            "MyTardis\n"
            % (user.username, user.id, site, authentication.id))

        try:
            mail.mail_admins(subject, message,
                             connection=get_connection(fail_silently=True))

        except Exception as e:
            logger.error("There was an error sending mail: %s ", e)

    return kwargs


@task(name="social_auth_account_approved", ignore_result=True)
def send_account_approved_email(user, authMethod):
    """Sends user email once account is approved by admin"""
    site_title = getattr(settings, 'SITE_TITLE', 'MyTardis')
    # get instruction link from settings
    account_migration_instructions_links = getattr(
        settings, 'ACCOUNT_MIGRATION_INSTRUCTIONS_LINKS',
        openid_migration_default_settings.ACCOUNT_MIGRATION_INSTRUCTIONS_LINKS)
    # get authenticated user backend
    account_migration_instructions_link = account_migration_instructions_links[authMethod]


    subject = '[MyTardis] User account Approved'
    message = (
        "Dear %s %s, \n\nWelcome to %s. \n\n"
        "Your account has been approved. "
        "Please use  the \"Sign in with %s\" button on the login page to "
        "log in to %s. "
        "If you have an existing %s account and would like to "
        "migrate your data and settings to your new account, "
        "follow the instructions on %s \n\n"
        "Thanks,\n"
        "MyTardis\n"
        % (user.first_name, user.last_name, site_title, authMethod,
           site_title, site_title, account_migration_instructions_link))
    try:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', default_settings.DEFAULT_FROM_EMAIL)
        user.email_user(
            subject, message, from_email=from_email, fail_silently=True)

    except Exception as e:
        logger.error("There was an error sending mail: %s ", e)


def migrate_user_message(**kwargs):
    """
    Automatically detects if a user has an account with the same email address
    and prompts user to perform migration.
    """
    # We don't need to provide any message if openid_migration app is not enabled
    if not is_openid_migration_enabled:
        return kwargs
    # Check if user accounts exist with the same email address
    user = kwargs.get('user')
    current_user_email = user.email
    users = User.objects.filter(email=current_user_email)
    if not users.count() > 1:
        return kwargs
    # Check if migration has been performed
    is_account_migrated = OpenidUserMigration.objects.filter(new_user=user)
    if is_account_migrated:
        return kwargs
    # check if account is not approved
    backend = kwargs.get('backend')
    authenticatedBackendName = type(backend).__name__
    # get auth method from backend
    authMethod = get_auth_method(authenticatedBackendName)
    user_auth = UserAuthentication.objects.get(userProfile=user.userprofile,
                       username=user.username,
                       authenticationMethod=authMethod,)

    if not user_auth or not user_auth.approved:
        return kwargs

    if not is_account_migrated:
        # update message
        request = kwargs.get('request')
        messages.add_message(request, messages.WARNING,
                             'We have found an existing account with your current email address. '
                             'Please migrate data from your old account by clicking '
                             '<a href="%s">Migrate My Account</a>'
                             % reverse('tardis.apps.openid_migration.views.migrate_accounts'),
                             extra_tags='safe')

    return kwargs


def is_openid_migration_enabled():
    try:
        if 'tardis.apps.openid_migration' in settings.INSTALLED_APPS:
            return getattr(settings, 'OPENID_MIGRATION_ENABLED', True)
    except AttributeError:
        pass
    return False


def requires_admin_approval(authenticationBackend):
    for authKey in social_auth_default_settings.ADMIN_APPROVAL_REQUIRED:
        if authenticationBackend == authKey:
            return authKey
    return None
