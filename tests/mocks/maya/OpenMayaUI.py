"""FAKE ``maya.OpenMayaUI``: no Maya main window exists in tests."""


class MQtUtil:
    @staticmethod
    def mainWindow():  # noqa: N802
        return None
