import json
from distutils.version import LooseVersion

import debug
import error_handler
import nakamori_player
import routing
from error_handler import try_function, show_messages, ErrorPriority, exception
from kodi_models import DirectoryListing, WatchedStatus
from nakamori_utils import kodi_utils, shoko_utils, script_utils, model_utils
from proxy.python_version_proxy import python_proxy as pyproxy
from nakamori_utils.globalvars import *
from windows import wizard, information

plugin_localize = plugin_addon.getLocalizedString
routing_plugin = routing.Plugin('plugin://plugin.video.nakamori', convert_args=True)
url_for = routing_plugin.url_for

# I had to read up on this. Functions have read access to this if they don't declare a plugin_dir
# if you want to do something like del plugin_dir, then you need to do this:
# def play():
#     global plugin_dir
#     del plugin_dir
plugin_dir = DirectoryListing()


def fail_menu():
    global plugin_dir
    plugin_dir.success = False
    del plugin_dir


def finish_menu():
    global plugin_dir
    del plugin_dir


# Order matters on these. In this, it goes try -> route -> show_main_menu
# Python is retarded, as you'd expect the opposite
@routing_plugin.route('/')
@try_function(ErrorPriority.BLOCKING)
def show_main_menu():
    version = LooseVersion(plugin_addon.getAddonInfo('version'))
    if version > LooseVersion(plugin_addon.getSetting('version')):
        fail_menu()
        information.open_information()
        restart_plugin()
        return

    from shoko_models.v2 import Filter
    f = Filter(0, build_full_object=True)
    plugin_dir.set_content('tvshows')
    items = []

    for item in f:
        items.append(item)
    # apply settings for main menu
    items[:] = [x for x in items if not is_main_menu_item_enabled(x)]

    add_extra_main_menu_items(items)

    # sort the filters
    try:
        # if they are all zero, preserve server sorting
        if any(x.sort_index != 0 for x in items):
            items.sort(key=lambda a: (a.sort_index, a.name))
    except:
        error_handler.exception(ErrorPriority.HIGH)
    for item in items:
        plugin_dir.append(item.get_listitem(), item.is_kodi_folder)


def is_main_menu_item_enabled(item):
    """

    :param item:
    :type item: Filter
    :return:
    """
    # This only has one at the moment, but we may add the ability for more later
    if item.name == 'Unsorted Files' and not plugin_addon.getSetting('show_unsort') == 'true':
        return False


def add_extra_main_menu_items(items):
    """
    Add items like search, calendar, etc
    :param items:
    :return:
    """
    from shoko_models.v2 import CustomItem
    # { 'Airing Today': 0, 'Calendar': 1, 'Seasons': 2, 'Years': 3, 'Tags': 4, 'Unsort': 5, 'Settings': 7,
    # 'Shoko Menu': 8, 'Search': 9 }
    if plugin_addon.getSetting('show_airing_today') == 'true':
        item = CustomItem(plugin_localize(30223), 'airing.png', url_for(show_airing_today_menu))
        item.sort_index = 1
        items.append(item)

    if plugin_addon.getSetting('show_calendar') == 'true':
        item = CustomItem(plugin_localize(30222), 'calendar.png', script(script_utils.url_calendar()))
        item.sort_index = 2
        item.is_kodi_folder = False
        items.append(item)

    if plugin_addon.getSetting('show_settings') == 'true':
        item = CustomItem(plugin_localize(30107), 'settings.png', script(script_utils.url_settings()))
        item.sort_index = 7
        item.is_kodi_folder = False
        items.append(item)

    if plugin_addon.getSetting('show_shoko') == 'true':
        item = CustomItem(plugin_localize(30115), 'settings.png', script(script_utils.url_shoko_menu()))
        item.sort_index = 8
        item.is_kodi_folder = False
        items.append(item)

    if plugin_addon.getSetting('show_search') == 'true':
        item = CustomItem(plugin_localize(30221), 'search.png', url_for(show_search_menu))
        item.sort_index = 9
        items.append(item)

    if plugin_addon.getSetting('onepunchmen') == 'true':
        item = CustomItem(plugin_localize(30145), 'airing.png', url_for(tvshow_menu, apikey=plugin_addon.getSetting('apikey')))
        item.sort_index = 99
        items.append(item)


@routing_plugin.route('/menu/filter/<filter_id>')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_filter_menu(filter_id):
    from shoko_models.v2 import Filter
    f = Filter(filter_id, build_full_object=True, get_children=True)
    plugin_dir.set_content('tvshows')
    plugin_dir.set_cached()
    f.add_sort_methods(routing_plugin.handle)
    for item in f:
        plugin_dir.append(item.get_listitem())

    finish_menu()
    f.apply_default_sorting()


@routing_plugin.route('/menu/group/<group_id>/filterby/<filter_id>')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_group_menu(group_id, filter_id):
    from shoko_models.v2 import Group
    group = Group(group_id, build_full_object=True, get_children=True, filter_id=filter_id)
    plugin_dir.set_content('tvshows')
    group.add_sort_methods(routing_plugin.handle)
    for item in group:
        plugin_dir.append(item.get_listitem())

    finish_menu()
    group.apply_default_sorting()


@routing_plugin.route('/menu/series/<series_id>')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_series_menu(series_id):
    from shoko_models.v2 import Series
    series = Series(series_id, build_full_object=True, get_children=True)

    if len(series.episode_types) > 1:
        plugin_dir.set_content('seasons')
        # type listing
        for item in series.episode_types:
            plugin_dir.append(item.get_listitem())
    elif len(series.episode_types) == 1:
        add_episodes(series, series.episode_types[0].episode_type)
    else:
        raise RuntimeError(plugin_localize(30152))


@routing_plugin.route('/menu/series/<series_id>/type/<episode_type>')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_series_episode_types_menu(series_id, episode_type):
    from shoko_models.v2 import SeriesTypeList
    types = SeriesTypeList(series_id, episode_type, get_children=True)
    add_episodes(types, episode_type)


def add_episodes(series, episode_type):
    from kodi_models import ListItem
    plugin_dir.set_content('episodes')
    series.add_sort_methods(routing_plugin.handle)
    select = kodi_utils.get_kodi_setting('videolibrary.tvshowsselectfirstunwatcheditem') > 0 \
        or plugin_addon.getSetting('select_unwatched') == 'true'
    watched_index = 0
    i = 0
    for item in series:
        try:
            if item.get_file() is None:
                continue
            listitem = item.get_listitem()
            assert isinstance(listitem, ListItem)
            if watched_index == i and item.is_watched() == WatchedStatus.WATCHED:
                watched_index += 1
            plugin_dir.append(listitem, False)
            i += 1
        except:
            exception(ErrorPriority.HIGHEST, plugin_localize(30153))

    add_continue_item(series, episode_type, watched_index)

    finish_menu()
    series.apply_default_sorting()
    if select:
        # the list is definitely not there yet, so try after 0.25s.
        xbmc.sleep(250)
        kodi_utils.move_to_index(watched_index)


def add_continue_item(series, episode_type, watched_index):
    if plugin_addon.getSetting('show_continue') != 'true':
        return
    from shoko_models.v2 import CustomItem
    continue_url = script(script_utils.url_move_to_item(watched_index))

    continue_text = plugin_localize(30053)
    if plugin_addon.getSetting('replace_continue') == 'true':
        eps = series.sizes.watched_episodes
        if plugin_addon.getSetting('local_only') == 'true':
            total = series.sizes.local_episodes
        else:
            total = series.sizes.total_episodes
        continue_text = '[ %s: %s/%s ]' % (episode_type, eps, total)

    continue_item = CustomItem(continue_text, '', continue_url, -1, False)
    continue_item.infolabels['episode'] = 0
    continue_item.infolabels['season'] = 0
    plugin_dir.insert(0, continue_item.get_listitem(), continue_item.is_kodi_folder)


@routing_plugin.route('/menu/airing_today')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_airing_today_menu():
    pass


@routing_plugin.route('/menu/calendar_old')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_calendar_menu():
    pass


@routing_plugin.route('/menu/filter/unsorted')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_unsorted_menu():
    # this is really bad practice, but the unsorted files list is too special
    from shoko_models.v2 import File
    url = server + '/api/file/unsort'
    json_body = pyproxy.get_json(url, True)
    json_node = json.loads(json_body)

    plugin_dir.set_content('episodes')
    for item in json_node:
        f = File(item)
        plugin_dir.append(f.get_listitem(), False)


@routing_plugin.route('/menu/search')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_search_menu():
    # search for new
    # quick search
    # clear search in context_menu
    from shoko_models.v2 import CustomItem
    plugin_dir.set_content('videos')

    clear_items = (plugin_localize(30110), script_utils.url_clear_search_terms())

    # Search
    item = CustomItem(plugin_localize(30224), 'new-search.png', script(script_utils.url_new_search(True)))
    item.is_kodi_folder = False
    item.set_context_menu_items([clear_items])
    plugin_dir.append(item.get_listitem())

    # quick search
    # TODO Setting for this, etc
    item = CustomItem(plugin_localize(30225), 'search.png', script(script_utils.url_new_search(False)))
    item.is_kodi_folder = False
    item.set_context_menu_items([clear_items])
    plugin_dir.append(item.get_listitem())

    import search
    # This is sorted by most recent
    search_history = search.get_search_history()
    for ss in search_history:
        try:
            query = ss[0]
            if len(query) == 0:
                continue
            item = CustomItem(query, 'search.png', url_for(show_search_result_menu, query))

            remove_item = (plugin_localize(30204), script_utils.url_remove_search_term(query))
            item.set_context_menu_items([remove_item, clear_items])

            plugin_dir.append(item.get_listitem())
        except:
            error_handler.exception(ErrorPriority.HIGHEST, plugin_localize(30151))


@routing_plugin.route('/menu/search/<path:query>')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def show_search_result_menu(query):
    search_url = server + '/api/search'
    search_url = model_utils.add_default_parameters(search_url, 0, 1)
    search_url = pyproxy.set_parameter(search_url, 'query', query)
    search_url = pyproxy.set_parameter(search_url, 'tags', 2)
    search_url = pyproxy.set_parameter(search_url, 'limit', plugin_addon.getSetting('maxlimit'))
    search_url = pyproxy.set_parameter(search_url, 'limit_tag', plugin_addon.getSetting('maxlimit_tag'))
    json_body = json.loads(pyproxy.get_json(search_url))
    groups = json_body['groups'][0]
    if groups['size'] == 0:
        # Show message about no results
        kodi_utils.message_box(plugin_localize(30180), plugin_localize(30181))
        fail_menu()
        return

    plugin_dir.set_content('tvshows')
    from shoko_models.v2 import Group, Series
    Group(0).add_sort_methods(routing_plugin.handle)
    for item in groups['series']:
        series = Series(item)
        plugin_dir.append(series.get_listitem())


def play_video_internal(ep_id, file_id, mark_as_watched=True, resume=False):
    # this prevents the spinning wheel
    fail_menu()

    if ep_id > 0 and file_id == 0:
        from shoko_models.v2 import Episode
        ep = Episode(ep_id, build_full_object=True)
        # follow pick_file setting
        if plugin_addon.getSetting('pick_file') == 'true':
            items = [(x.name, x.id) for x in ep]
            selected_id = kodi_utils.show_file_list(items)
        else:
            selected_id = ep.get_file().id
    else:
        selected_id = file_id

    # all of real work is done here
    nakamori_player.play_video(selected_id, ep_id, mark_as_watched, resume)


@routing_plugin.route('/episode/<ep_id>/file/<file_id>/play')
@try_function(ErrorPriority.BLOCKING)
def play_video(ep_id, file_id):
    play_video_internal(ep_id, file_id)


@routing_plugin.route('/episode/<ep_id>/file/<file_id>/play_without_marking')
def play_video_without_marking(ep_id, file_id):
    play_video_internal(ep_id, file_id, mark_as_watched=False)


@routing_plugin.route('/episode/<ep_id>/file/<file_id>/resume')
@try_function(ErrorPriority.BLOCKING)
def resume_video(ep_id, file_id):
    # if we are resuming, then we'll assume that scrobbling and marking are True
    play_video_internal(ep_id, file_id, mark_as_watched=True, resume=True)


def script(script_url):
    return url_for(run_script, script_url)


@routing_plugin.route('/script/<path:script_url>')
def run_script(script_url):
    global plugin_dir
    plugin_dir.success = False
    del plugin_dir

    xbmc.executebuiltin(script_url)


def restart_plugin():
    script_utils.arbiter(0, 'RunAddon("plugin.video.nakamori")')


@routing_plugin.route('/tvshows/<apikey>/')
@try_function(ErrorPriority.BLOCKING, except_func=fail_menu)
def tvshow_menu(apikey):
    from shoko_models.v2 import Filter, set_in_memory_apikey
    set_in_memory_apikey(apikey)
    filter_id = 1  # until everything is fixed lets use continue-watching
    f = Filter(filter_id, build_full_object=True, get_children=True)
    plugin_dir.set_content('tvshows')
    plugin_dir.set_cached()
    for item in f:
        plugin_dir.append(item.get_listitem())

    finish_menu()


@try_function(ErrorPriority.BLOCKING)
def main():
    debug.debug_init()
    # stage 1 - check connection
    if not shoko_utils.can_connect():
        fail_menu()
        kodi_utils.message_box(plugin_localize(30159), plugin_localize(30154))
        if wizard.open_connection_wizard():
            restart_plugin()
            return
        if not shoko_utils.can_connect():
            raise RuntimeError(plugin_localize(30155))

    # stage 2 - Check server startup status
    if not shoko_utils.get_server_status():
        return

    # stage 3 - auth
    auth = shoko_utils.auth()
    if not auth:
        fail_menu()
        kodi_utils.message_box(plugin_localize(30156), plugin_localize(30157))
        if wizard.open_login_wizard():
            restart_plugin()
            return
        auth = shoko_utils.auth()
        if not auth:
            raise RuntimeError(plugin_localize(30158))

    routing_plugin.run()
    show_messages()


if __name__ == '__main__':
    main()
