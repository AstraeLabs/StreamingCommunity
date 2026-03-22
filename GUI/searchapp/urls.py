# 06.06.25

from django.urls import path

from . import views

urlpatterns = [
    path("", views.search_home, name="search_home"),
    path("search/", views.search, name="search"),
    path("download/", views.start_download, name="start_download"),
    path("series-metadata/", views.series_metadata, name="series_metadata"),
    path("series-detail/", views.series_detail, name="series_detail"),

    # Download
    path("downloads/", views.download_dashboard, name="download_dashboard"),
    path("api/get-downloads/", views.get_downloads_json, name="get_downloads_json"),
    path("api/kill-download/", views.kill_download, name="kill_download"),
    path("api/kill-and-clear-queue/", views.kill_and_clear_queue, name="kill_and_clear_queue"),
    path("api/clear-history/", views.clear_download_history, name="clear_download_history"),
    
    # Watchlist
    path("watchlist/", views.watchlist, name="watchlist"),
    path("watchlist/add/", views.add_to_watchlist, name="add_to_watchlist"),
    path("watchlist/remove/<int:item_id>/", views.remove_from_watchlist, name="remove_from_watchlist"),
    path("watchlist/update/<int:item_id>/", views.update_watchlist_item, name="update_watchlist_item"),
    path("watchlist/update-all/", views.update_all_watchlist, name="update_all_watchlist"),
    path("watchlist/auto/<int:item_id>/", views.update_watchlist_auto, name="update_watchlist_auto"),
    path("watchlist/auto-run/", views.run_watchlist_auto_now, name="run_watchlist_auto_now"),
    path("watchlist/auto-interval/", views.set_watchlist_polling_interval, name="set_watchlist_polling_interval"),
    path("watchlist/clear/", views.clear_watchlist, name="clear_watchlist"),
    path("api/watchlist-status/", views.watchlist_status, name="watchlist_status"),
]