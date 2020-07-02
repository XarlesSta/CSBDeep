from __future__ import print_function, unicode_literals, absolute_import, division
from six.moves import range, zip, map, reduce, filter

import numpy as np
import os
import warnings
import shutil
import datetime
from importlib import import_module

from tensorflow import __version__ as _tf_version
IS_TF_1 = _tf_version.startswith('1.')
_KERAS = 'keras' if IS_TF_1 else 'tensorflow.keras'

def keras_import(sub=None, *names):
    if sub is None:
        return import_module(_KERAS)
    else:
        mod = import_module('{_KERAS}.{sub}'.format(_KERAS=_KERAS,sub=sub))
        if len(names) == 0:
            return mod
        elif len(names) == 1:
            return getattr(mod, names[0])
        return tuple(getattr(mod, name) for name in names)

if IS_TF_1:
    import tensorflow as tf
else:
    import tensorflow.compat.v1 as tf
    # tf.disable_v2_behavior()

keras = keras_import()
K = keras_import('backend')
Callback = keras_import('callbacks', 'Callback')
Lambda = keras_import('layers', 'Lambda')

from .utils import _raise, is_tf_backend, save_json, backend_channels_last
from .six import tempfile



def limit_gpu_memory(fraction, allow_growth=False, total_memory=None):
    """Limit GPU memory allocation for TensorFlow (TF) backend.

    Parameters
    ----------
    fraction : float
        Limit TF to use only a fraction (value between 0 and 1) of the available GPU memory.
        Reduced memory allocation can be disabled if fraction is set to ``None``.
    allow_growth : bool, optional
        If ``False`` (default), TF will allocate all designated (see `fraction`) memory all at once.
        If ``True``, TF will allocate memory as needed up to the limit imposed by `fraction`; this may
        incur a performance penalty due to memory fragmentation.
    total_memory :  int or iterable of int
        Total amount of available GPU memory (in MB).

    Raises
    ------
    ValueError
        If `fraction` is not ``None`` or a float value between 0 and 1.
    NotImplementedError
        If TensorFlow is not used as the backend.
    """

    is_tf_backend() or _raise(NotImplementedError('Not using tensorflow backend.'))
    fraction is None or (np.isscalar(fraction) and 0<=fraction<=1) or _raise(ValueError('fraction must be between 0 and 1.'))

    if IS_TF_1:
        _session = None
        try:
            _session = K.tensorflow_backend._SESSION
        except AttributeError:
            pass

        if _session is None:
            config = tf.ConfigProto()
            if fraction is not None:
                config.gpu_options.per_process_gpu_memory_fraction = fraction
            config.gpu_options.allow_growth = bool(allow_growth)
            session = tf.Session(config=config)
            K.tensorflow_backend.set_session(session)
            # print("[tf_limit]\t setting config.gpu_options.per_process_gpu_memory_fraction to ",config.gpu_options.per_process_gpu_memory_fraction)
        else:
            warnings.warn('Too late to limit GPU memory, can only be done once and before any computation.')
    else:
        gpus = tf.config.experimental.list_physical_devices('GPU')
        if gpus:
            if fraction is not None:
                np.isscalar(total_memory) or _raise(ValueError("'total_memory' must be provided when using TensorFlow 2."))
                vdc = tf.config.experimental.VirtualDeviceConfiguration(memory_limit=int(np.ceil(total_memory*fraction)))
            try:
                for gpu in gpus:
                    if fraction is not None:
                        tf.config.experimental.set_virtual_device_configuration(gpu,[vdc])
                    if allow_growth:
                        tf.config.experimental.set_memory_growth(gpu, True)
            except RuntimeError as e:
                # must be set before GPUs have been initialized
                print(e)



def export_SavedModel(model, outpath, meta={}, format='zip'):
    """Export Keras model in TensorFlow's SavedModel_ format.

    See `Your Model in Fiji`_ to learn how to use the exported model with our CSBDeep Fiji plugins.

    .. _SavedModel: https://www.tensorflow.org/programmers_guide/saved_model#structure_of_a_savedmodel_directory
    .. _`Your Model in Fiji`: https://github.com/CSBDeep/CSBDeep_website/wiki/Your-Model-in-Fiji

    Parameters
    ----------
    model : :class:`keras.models.Model`
        Keras model to be exported.
    outpath : str
        Path of the file/folder that the model will exported to.
    meta : dict, optional
        Metadata to be saved in an additional ``meta.json`` file.
    format : str, optional
        Can be 'dir' to export as a directory or 'zip' (default) to export as a ZIP file.

    Raises
    ------
    ValueError
        Illegal arguments.

    """

    def export_to_dir(dirname):
        if len(model.inputs) > 1 or len(model.outputs) > 1:
            warnings.warn('Found multiple input or output layers.')
        if IS_TF_1:
            builder = tf.saved_model.builder.SavedModelBuilder(dirname)
            # use name 'input'/'output' if there's just a single input/output layer
            inputs  = dict(zip(model.input_names,model.inputs))   if len(model.inputs)  > 1 else dict(input=model.input)
            outputs = dict(zip(model.output_names,model.outputs)) if len(model.outputs) > 1 else dict(output=model.output)
            signature = tf.saved_model.signature_def_utils.predict_signature_def(inputs=inputs, outputs=outputs)
            signature_def_map = { tf.saved_model.signature_constants.DEFAULT_SERVING_SIGNATURE_DEF_KEY: signature }
            builder.add_meta_graph_and_variables(K.get_session(),
                                                 [tf.saved_model.tag_constants.SERVING],
                                                 signature_def_map=signature_def_map)
            builder.save()
        else:
            warnings.warn('Model export in TensorFlow 2 is untested.')
            tf.saved_model.save(model, dirname)
        if meta is not None and len(meta) > 0:
            save_json(meta, os.path.join(dirname,'meta.json'))


    ## checks
    isinstance(model,keras.models.Model) or _raise(ValueError("'model' must be a Keras model."))
    # supported_formats = tuple(['dir']+[name for name,description in shutil.get_archive_formats()])
    supported_formats = 'dir','zip'
    format in supported_formats or _raise(ValueError("Unsupported format '%s', must be one of %s." % (format,str(supported_formats))))

    # remove '.zip' file name extension if necessary
    if format == 'zip' and outpath.endswith('.zip'):
        outpath = os.path.splitext(outpath)[0]

    if format == 'dir':
        export_to_dir(outpath)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpsubdir = os.path.join(tmpdir,'model')
            export_to_dir(tmpsubdir)
            shutil.make_archive(outpath, format, tmpsubdir)



def tf_normalize(x, pmin=1, pmax=99.8, axis=None, clip=False):
    assert pmin < pmax
    mi = tf.contrib.distributions.percentile(x,pmin, axis=axis, keep_dims=True)
    ma = tf.contrib.distributions.percentile(x,pmax, axis=axis, keep_dims=True)
    y = (x-mi)/(ma-mi+K.epsilon())
    if clip:
        y = K.clip(y,0,1.0)
    return y


def tf_normalize_layer(layer, pmin=1, pmax=99.8, clip=True):
    def norm(x,axis):
        return tf_normalize(x, pmin=pmin, pmax=pmax, axis=axis, clip=clip)

    shape = K.int_shape(layer)
    n_channels_out = shape[-1]
    n_dim_out = len(shape)

    if n_dim_out > 4:
        layer = Lambda(lambda x: K.max(x, axis=tuple(range(1,1+n_dim_out-4))))(layer)

    assert 0 < n_channels_out

    if n_channels_out == 1:
        out = Lambda(lambda x: norm(x, axis=(1,2)))(layer)
    elif n_channels_out == 2:
        out = Lambda(lambda x: norm(K.concatenate([x,x[...,:1]], axis=-1), axis=(1,2,3)))(layer)
    elif n_channels_out == 3:
        out = Lambda(lambda x: norm(x, axis=(1,2,3)))(layer)
    else:
        out = Lambda(lambda x: norm(K.max(x, axis=-1, keepdims=True), axis=(1,2,3)))(layer)
    return out


class CARETensorBoard(Callback):
    """ TODO """
    def __init__(self, log_dir='./logs',
                 freq=1,
                 compute_histograms=False,
                 n_images=3,
                 prob_out=False,
                 write_graph=False,
                 prefix_with_timestamp=True,
                 write_images=False,
                 image_for_inputs=None,  # write images for only these input indices
                 image_for_outputs=None, # write images for only these output indices
                 input_slices=None,      # list (of list) of slices to apply to `image_for_inputs` layers before writing image
                 output_slices=None):    # list (of list) of slices to apply to `image_for_outputs` layers before writing image
        super(CARETensorBoard, self).__init__()
        is_tf_backend() or _raise(RuntimeError('TensorBoard callback only works with the TensorFlow backend.'))
        backend_channels_last() or _raise(NotImplementedError())

        self.freq = freq
        self.image_freq = freq
        self.prob_out = prob_out
        self.merged = None
        self.gt_outputs = None
        self.write_graph = write_graph
        self.write_images = write_images
        self.n_images = n_images
        self.image_for_inputs = image_for_inputs
        self.image_for_outputs = image_for_outputs
        self.input_slices = input_slices
        self.output_slices = output_slices
        self.compute_histograms = compute_histograms

        if prefix_with_timestamp:
            log_dir = os.path.join(log_dir, datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S.%f"))

        self.log_dir = log_dir

    def set_model(self, model):
        self.model = model
        self.sess = K.get_session()
        tf_sums = []

        if self.compute_histograms and self.freq and self.merged is None:
            for layer in self.model.layers:
                for weight in layer.weights:
                    tf_sums.append(tf.summary.histogram(weight.name, weight))

                if hasattr(layer, 'output'):
                    tf_sums.append(tf.summary.histogram('{}_out'.format(layer.name),
                                                        layer.output))


        def _gt_shape(output_shape):
            if not self.prob_out: return output_shape
            output_shape[-1] % 2 == 0 or _raise(ValueError())
            return list(output_shape[:-1]) + [output_shape[-1] // 2]
        self.gt_outputs = [K.placeholder(shape=_gt_shape(K.int_shape(x))) for x in self.model.outputs]

        n_inputs, n_outputs = len(self.model.inputs), len(self.model.outputs)
        image_for_inputs  = np.arange(n_inputs)  if self.image_for_inputs  is None else self.image_for_inputs
        image_for_outputs = np.arange(n_outputs) if self.image_for_outputs is None else self.image_for_outputs

        input_slices  = (slice(None),) if self.input_slices  is None else self.input_slices
        output_slices = (slice(None),) if self.output_slices is None else self.output_slices
        if isinstance(input_slices[0],slice): # apply same slices to all inputs
            input_slices = [input_slices]*len(image_for_inputs)
        if isinstance(output_slices[0],slice): # apply same slices to all outputs
            output_slices = [output_slices]*len(image_for_outputs)
        len(input_slices)  == len(image_for_inputs)  or _raise(ValueError())
        len(output_slices) == len(image_for_outputs) or _raise(ValueError())

        def _name(prefix, layer, i, n, show_layer_names=False):
            return '{prefix}{i}{name}'.format (
                prefix = prefix,
                i      = (i if n > 1 else ''),
                name   = '' if (layer is None or not show_layer_names) else '_'+''.join(layer.name.split(':')[:-1]),
            )

        # inputs
        for i,sl in zip(image_for_inputs,input_slices):
            # print('input', self.model.inputs[i], tuple(sl))
            layer_name = _name('net_input', self.model.inputs[i], i, n_inputs)
            input_layer = tf_normalize_layer(self.model.inputs[i][tuple(sl)])
            tf_sums.append(tf.summary.image(layer_name, input_layer, max_outputs=self.n_images))

        # outputs
        for i,sl in zip(image_for_outputs,output_slices):
            # print('output', self.model.outputs[i], tuple(sl))
            output_shape = self.model.output_shape if n_outputs==1 else self.model.output_shape[i]
            # target
            output_layer = tf_normalize_layer(self.gt_outputs[i][tuple(sl)])
            layer_name = _name('net_target', self.model.outputs[i], i, n_outputs)
            tf_sums.append(tf.summary.image(layer_name, output_layer, max_outputs=self.n_images))
            # prediction
            n_channels_out = sep = output_shape[-1]
            if self.prob_out: # first half of output channels is mean, second half scale
                n_channels_out % 2 == 0 or _raise(ValueError())
                sep = sep // 2
            output_layer = tf_normalize_layer(self.model.outputs[i][...,:sep][tuple(sl)])
            if self.prob_out:
                scale_layer = tf_normalize_layer(self.model.outputs[i][...,sep:][tuple(sl)], pmin=0, pmax=100)
                mean_name  = _name('net_output_mean',  self.model.outputs[i], i, n_outputs)
                scale_name = _name('net_output_scale', self.model.outputs[i], i, n_outputs)
                tf_sums.append(tf.summary.image(mean_name, output_layer, max_outputs=self.n_images))
                tf_sums.append(tf.summary.image(scale_name, scale_layer, max_outputs=self.n_images))
            else:
                layer_name = _name('net_output', self.model.outputs[i], i, n_outputs)
                tf_sums.append(tf.summary.image(layer_name, output_layer, max_outputs=self.n_images))


        with tf.name_scope('merged'):
            self.merged = tf.summary.merge(tf_sums)

        with tf.name_scope('summary_writer'):
            if self.write_graph:
                self.writer = tf.summary.FileWriter(self.log_dir,
                                                    self.sess.graph)
            else:
                self.writer = tf.summary.FileWriter(self.log_dir)

    def on_epoch_end(self, epoch, logs=None):
        logs = logs or {}

        if self.validation_data and self.freq:
            if epoch % self.freq == 0:
                # TODO: implement batched calls to sess.run
                # (current call will likely go OOM on GPU)

                tensors = self.model.inputs + self.gt_outputs + self.model.sample_weights

                if self.model.uses_learning_phase:
                    tensors += [K.learning_phase()]
                    val_data = list(v[:self.n_images] for v in self.validation_data[:-1])
                    val_data += self.validation_data[-1:]
                else:
                    val_data = list(v[:self.n_images] for v in self.validation_data)

                feed_dict = dict(zip(tensors, val_data))
                result = self.sess.run([self.merged], feed_dict=feed_dict)
                summary_str = result[0]

                self.writer.add_summary(summary_str, epoch)

        for name, value in logs.items():
            if name in ['batch', 'size']:
                continue
            summary = tf.Summary()
            summary_value = summary.value.add()
            summary_value.simple_value = float(value)
            summary_value.tag = name
            self.writer.add_summary(summary, epoch)
        self.writer.flush()

    def on_train_end(self, _):
        self.writer.close()
