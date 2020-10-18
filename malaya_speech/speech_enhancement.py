from malaya_speech.path import (
    PATH_SPEECH_ENHANCEMENT,
    S3_PATH_SPEECH_ENHANCEMENT,
)
from malaya_speech.supervised import unet
from herpetologist import check_type

_availability = {
    'resnet34-unet': {'Size (MB)': 97.8, 'MSE': 0.0003},
    'inception-v3-unet': {'Size (MB)': 120, 'MSE': 0.0003},
}


def available_model():
    """
    List available Speech Enhancement deep learning models.
    """
    from malaya_speech.utils import describe_availability

    return describe_availability(_availability)


@check_type
def deep_model(model: str = 'resnet34-unet', **kwargs):
    """
    Load Speech Enhancement deep learning model.

    Parameters
    ----------
    model : str, optional (default='wavenet')
        Model architecture supported. Allowed values:

        * ``'resnet34-unet'`` - pretrained resnet34 UNET.
        * ``'inception-v3-unet'`` - pretrained inception V3 UNET.

    Returns
    -------
    result : malaya_speech.model.tf.SEGMENTATION class
    """

    model = model.lower()
    if model not in _availability:
        raise Exception(
            'model not supported, please check supported models from malaya_speech.speech_enhancement.available_model()'
        )

    return unet.load(
        path = PATH_SPEECH_ENHANCEMENT,
        s3_path = S3_PATH_SPEECH_ENHANCEMENT,
        model = model,
        name = 'speech-enhancement',
        **kwargs
    )
