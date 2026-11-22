"""QGIS entry point for Campus3D GIS Feedback v0.7.2."""

def classFactory(iface):
    from .plugin import Campus3DGISFeedbackPlugin
    return Campus3DGISFeedbackPlugin(iface)
