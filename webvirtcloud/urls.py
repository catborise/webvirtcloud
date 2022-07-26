from appsettings.views import appsettings
from console.views import console
from django.conf import settings
from django.urls import include, path
from instances.views import index
from rest_framework_nested import routers
from computes.api.viewsets import ComputeViewSet
from storages.api.viewsets import StorageViewSet, VolumeViewSet
from instances.api.viewsets import InstanceViewSet, MigrateViewSet


router = routers.SimpleRouter()
router.register(r'computes', ComputeViewSet)
router.register(r'instances', InstanceViewSet, basename='instance')
router.register(r'migrate', MigrateViewSet, basename='instance')

compute_router = routers.NestedSimpleRouter(router, r'computes', lookup='compute')
compute_router.register(r'instances', InstanceViewSet, basename='compute-instances')
compute_router.register(r'storages', StorageViewSet, basename='compute-storages')

urlpatterns = [
    path("", index, name="index"),
    path("admin/", include(("admin.urls", "admin"), namespace="admin")),
    path("accounts/", include("accounts.urls")),
    path("appsettings/", appsettings, name="appsettings"),
    path("computes/", include("computes.urls")),
    path("console/", console, name="console"),
    path("datasource/", include("datasource.urls")),
    path("instances/", include("instances.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("logs/", include("logs.urls")),
    path('api-auth/', include('rest_framework.urls', namespace='rest_framework')),
    path('api/v1/', include(router.urls)),
    path('api/v1/', include(compute_router.urls)),
]

if settings.DEBUG:
    try:
        import debug_toolbar

        urlpatterns += [
            path("__debug__/", include(debug_toolbar.urls)),
        ]
    except ImportError:
        pass

