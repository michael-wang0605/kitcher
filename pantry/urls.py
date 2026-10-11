from django.urls import path

from pantry import cooking_views, import_views, operations_views, recipe_views, views

urlpatterns = [
    path("status/", operations_views.operations, name="status"),
    path("imports/", import_views.import_list, name="imports"),
    path("imports/<uuid:pk>/", import_views.import_review, name="import_review"),
    path("imports/<uuid:pk>/source/", import_views.import_source, name="import_source"),
    path(
        "imports/<uuid:pk>/cancel/",
        import_views.import_action,
        {"action": "cancel"},
        name="import_cancel",
    ),
    path(
        "imports/<uuid:pk>/retry/",
        import_views.import_action,
        {"action": "retry"},
        name="import_retry",
    ),
    path("recipes/<int:pk>/cook/", cooking_views.cook_recipe, name="cook"),
    path(
        "recipes/<int:pk>/archive/", cooking_views.archive_recipe, name="recipe_archive"
    ),
    path("history/<int:pk>/reverse/", cooking_views.reverse_event, name="reverse"),
    path("history/<int:pk>/correct/", cooking_views.correct_event, name="correction"),
    path("recipes/", recipe_views.recipe_book, name="recipes"),
    path("recipes/new/", recipe_views.edit_recipe, name="recipe_new"),
    path("recipes/<int:pk>/", recipe_views.edit_recipe, name="recipe_edit"),
    path("shopping/", recipe_views.shopping_view, name="shopping"),
    path("shopping/selection/", recipe_views.edit_selection, name="selection_edit"),
    path("", views.pantry, name="pantry"),
    path("ingredients/", views.ingredients, name="ingredients"),
    path("inventory/preview/", views.preview_update, name="preview"),
    path("inventory/confirm/", views.confirm_update, name="confirm"),
    path("history/", views.history, name="history"),
    path("history/<int:pk>/", views.event_detail, name="event"),
]
