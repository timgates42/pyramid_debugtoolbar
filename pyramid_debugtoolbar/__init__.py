from pyramid.config import Configurator
from pyramid.settings import asbool
import pyramid.tweens
from pyramid_debugtoolbar.utils import (
    as_cr_separated_list,
    as_display_debug_or_false,
    as_globals_list,
    as_int,
    as_list,
    EXC_ROUTE_NAME,
    ROOT_ROUTE_NAME,
    SETTINGS_PREFIX,
    STATIC_PATH,
)
from pyramid_debugtoolbar.toolbar import (
    IRequestAuthorization,
    IToolbarWSGIApp,
    toolbar_tween_factory,
)

toolbar_tween_factory = toolbar_tween_factory  # API

default_panel_names = (
    'pyramid_debugtoolbar.panels.headers.HeaderDebugPanel',
    'pyramid_debugtoolbar.panels.logger.LoggingPanel',
    'pyramid_debugtoolbar.panels.performance.PerformanceDebugPanel',
    'pyramid_debugtoolbar.panels.renderings.RenderingsDebugPanel',
    'pyramid_debugtoolbar.panels.request_vars.RequestVarsDebugPanel',
    'pyramid_debugtoolbar.panels.sqla.SQLADebugPanel',
    'pyramid_debugtoolbar.panels.traceback.TracebackPanel',
)

default_global_panel_names = (
    'pyramid_debugtoolbar.panels.introspection.IntrospectionDebugPanel',
    'pyramid_debugtoolbar.panels.routes.RoutesDebugPanel',
    'pyramid_debugtoolbar.panels.settings.SettingsDebugPanel',
    'pyramid_debugtoolbar.panels.tweens.TweensDebugPanel',
    'pyramid_debugtoolbar.panels.versions.VersionDebugPanel',
)

default_hosts = ('127.0.0.1', '::1')

default_settings = [
    # name, convert, default
    ('enabled', asbool, 'true'),
    ('intercept_exc', as_display_debug_or_false, 'debug'),
    ('intercept_redirects', asbool, 'false'),
    ('panels', as_globals_list, default_panel_names),
    ('extra_panels', as_globals_list, ()),
    ('global_panels', as_globals_list, default_global_panel_names),
    ('extra_global_panels', as_globals_list, ()),
    ('hosts', as_list, default_hosts),
    ('exclude_prefixes', as_cr_separated_list, []),
    ('active_panels', as_list, ()),
    ('includes', as_list, ()),
    ('button_style', None, ''),
    ('max_request_history', as_int, 100),
    ('max_visible_requests', as_int, 10),
]

# We need to transform these from debugtoolbar. to pyramid. in our
# make_application, but we want to allow people to set them in their
# configurations as debugtoolbar.
default_transform = [
    # name, convert, default
    ('debug_notfound', asbool, 'false'),
    ('debug_routematch', asbool, 'false'),
    ('prevent_http_cache', asbool, 'false'),
    ('reload_assets', asbool, 'false'),
    ('reload_resources', asbool, 'false'),
    ('reload_templates', asbool, 'false'),
]


def parse_settings(settings):
    parsed = {}

    def populate(name, convert, default):
        name = '%s%s' % (SETTINGS_PREFIX, name)
        value = settings.get(name, default)
        if convert is not None:
            value = convert(value)
        parsed[name] = value

    # Extend the ones we are going to transform later ...
    cfg = list(default_settings)
    cfg.extend(default_transform)

    # Convert to the proper format ...
    for name, convert, default in cfg:
        populate(name, convert, default)

    return parsed

def transform_settings(settings):
    parsed = {}

    def populate(name):
        oname = '%s%s' % (SETTINGS_PREFIX, name)
        nname = 'pyramid.%s' % name
        value = settings.get(oname, False)
        parsed[nname] = value

    for name, _, _ in default_transform:
        populate(name)

    return parsed

def set_request_authorization_callback(config, callback):
    """
    Register IRequestAuthorization utility to authorize toolbar per request.
    """
    config.registry.registerUtility(callback, IRequestAuthorization)

def inject_toolbar(event):
    app = event.app
    registry = app.registry

    # inject the BeforeRender subscriber after the application is created
    # and all other subscribers are registered in hopes that this will be
    # the last subscriber in the chain and will be able to see the effects
    # of all previous subscribers on the event
    config = Configurator(registry=registry, introspection=False)
    config.add_subscriber(
        'pyramid_debugtoolbar.toolbar.beforerender_subscriber',
        'pyramid.events.BeforeRender',
    )
    config.commit()

def includeme(config):
    """ Activate the debug toolbar; usually called via
    ``config.include('pyramid_debugtoolbar')`` instead of being invoked
    directly. """
    introspection = getattr(config, 'introspection', True)
    # dont register any introspectables for Pyramid 1.3a9+
    config.introspection = False

    # Parse the settings
    settings = parse_settings(config.registry.settings)

    # Update the current registry with the new settings
    config.registry.settings.update(settings)

    # Do the transform and update the settings dictionary
    settings.update(transform_settings(settings))

    # Create the toolbar application using the updated settings
    # Do this before adding the tween, etc to give debugtoolbar.includes
    # a chance to affect the settings beforehand incase autocommit is
    # enabled
    application = make_application(settings, config.registry)
    config.registry.registerUtility(application, IToolbarWSGIApp)

    config.add_tween(
        'pyramid_debugtoolbar.toolbar_tween_factory',
        over=[
            pyramid.tweens.EXCVIEW,
            'pyramid_tm.tm_tween_factory',
        ],
    )
    config.add_subscriber(inject_toolbar, 'pyramid.events.ApplicationCreated')
    config.add_directive('set_debugtoolbar_request_authorization',
                         set_request_authorization_callback)

    # register routes and views that can be used within the tween
    config.add_route('debugtoolbar', '/_debug_toolbar/*subpath', static=True)
    config.add_static_view('/_debug_toolbar/static', STATIC_PATH, static=True)

    config.introspection = introspection

def make_application(settings, parent_registry):
    """ WSGI application for rendering the debug toolbar."""
    config = Configurator(settings=settings)
    config.registry.parent_registry = parent_registry
    config.include('pyramid_mako')
    config.add_directive('add_debugtoolbar_panel', add_debugtoolbar_panel)
    config.add_mako_renderer('.dbtmako', settings_prefix='dbtmako.')
    config.add_static_view('static', STATIC_PATH)
    config.add_route(ROOT_ROUTE_NAME, '/', static=True)
    config.add_route('debugtoolbar.sse', '/sse')
    config.add_route('debugtoolbar.source', '/source')
    config.add_route('debugtoolbar.execute', '/execute')
    config.add_route('debugtoolbar.console', '/console')
    config.add_route('debugtoolbar.redirect', '/redirect')
    config.add_route(EXC_ROUTE_NAME, '/exception')
    config.add_route(
        'debugtoolbar.sql_select',
        '/{request_id}/sqlalchemy/select/{query_index}')
    config.add_route(
        'debugtoolbar.sql_explain',
        '/{request_id}/sqlalchemy/explain/{query_index}')
    config.add_route('debugtoolbar.request', '/{request_id}')
    config.add_route('debugtoolbar.main', '/')
    config.scan('.views')

    # commit the toolbar config and include any user-defined includes
    config.commit()

    includes = settings.get(SETTINGS_PREFIX + 'includes', ())
    for include in includes:
        config.include(include)

    return config.make_wsgi_app()

def add_debugtoolbar_panel(config, panel_factory, is_global=False):
    """
    A Pyramid config directive accessible as ``config.add_debugtoolbar_panel``.

    This directive can add a new panel to the toolbar application. It should
    be used from includeme functions via the ``debugtoolbar.includes`` setting.

    The ``panel_factory`` should be a factory that accepts a ``request``
    object and returns a subclass of
    :class:`pyramid_debugtoolbar.panels.DebugPanel`.

    If ``is_global`` is ``True`` then the panel will be added to the global
    panel list which includes application-wide panels that do not depend
    on per-request data to operate.

    """
    parent_settings = config.registry.parent_registry.settings
    if is_global:
        default_setting = SETTINGS_PREFIX + 'global_panels'
        extra_setting = SETTINGS_PREFIX + 'extra_global_panels'
    else:
        default_setting = SETTINGS_PREFIX + 'panels'
        extra_setting = SETTINGS_PREFIX + 'extra_panels'
    default_panels = parent_settings.get(default_setting, [])
    extra_panels = parent_settings.setdefault(extra_setting, [])

    # only add the panel if it wasn't added manually in an explicit order
    if (
        panel_factory not in extra_panels and
        panel_factory not in default_panels
    ):
        default_panels.append(panel_factory)
