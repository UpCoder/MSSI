# -*- coding=utf-8 -*-
# Copyright 2015 Paul Balanca. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Pre-processing images for SSD-type networks.
"""
from enum import Enum, IntEnum
import numpy as np

import tensorflow as tf
import tf_extended as tfe

from tensorflow.python.ops import control_flow_ops
import cv2
import util
from preprocessing import tf_image_multiphase_multislice_mask

slim = tf.contrib.slim

# Resizing strategies.
Resize = IntEnum('Resize', ('NONE',                # Nothing!
                            'CENTRAL_CROP',        # Crop (and pad if necessary).
                            'PAD_AND_RESIZE',      # Pad, and resize to output shape.
                            'WARP_RESIZE'))        # Warp resize.

import config
# VGG mean parameters.
_R_MEAN = config.r_mean
_G_MEAN = config.g_mean
_B_MEAN = config.b_mean

# Some training pre-processing parameters.
MAX_EXPAND_SCALE = config.max_expand_scale
BBOX_CROP_OVERLAP = config.bbox_crop_overlap        # Minimum overlap to keep a bbox after cropping.
MIN_OBJECT_COVERED = config.min_object_covered
CROP_ASPECT_RATIO_RANGE = config.crop_aspect_ratio_range  # Distortion ratio during cropping.
AREA_RANGE = config.area_range
FLIP = config.flip
LABEL_IGNORE = config.ignore_label
USING_SHORTER_SIDE_FILTERING = config.using_shorter_side_filtering

MIN_SHORTER_SIDE = config.min_shorter_side
MAX_SHORTER_SIDE = config.max_shorter_side

USE_ROTATION = config.use_rotation

def tf_image_whitened(image, means=[_R_MEAN, _G_MEAN, _B_MEAN]):
    """Subtracts the given means from each image channel.

    Returns:
        the centered image.
    """
    if image.get_shape().ndims != 3:
        raise ValueError('Input must be of size [height, width, C>0]')
    num_channels = image.get_shape().as_list()[-1]
    if len(means) != num_channels:
        raise ValueError('len(means) must match the number of channels')

    mean = tf.constant(means, dtype=image.dtype)
    image = image - mean
    return image


def tf_image_unwhitened(image, means=[_R_MEAN, _G_MEAN, _B_MEAN], to_int=True):
    """Re-convert to original image distribution, and convert to int if
    necessary.

    Returns:
      Centered image.
    """
    mean = tf.constant(means, dtype=image.dtype)
    image = image + mean
    if to_int:
        image = tf.cast(image, tf.int32)
    return image


def np_image_unwhitened(image, means=[_R_MEAN, _G_MEAN, _B_MEAN], to_int=True):
    """Re-convert to original image distribution, and convert to int if
    necessary. Numpy version.

    Returns:
      Centered image.
    """
    img = np.copy(image)
    img += np.array(means, dtype=img.dtype)
    if to_int:
        img = img.astype(np.uint8)
    return img


def tf_summary_image(image, bboxes, name='image', unwhitened=False):
    """Add image with bounding boxes to summary.
    """
    if unwhitened:
        image = tf_image_unwhitened(image)
    image = tf.expand_dims(image, 0)
    bboxes = tf.expand_dims(bboxes, 0)
    image_with_box = tf.image.draw_bounding_boxes(image, bboxes)
    tf.summary.image(name, image_with_box)


def apply_with_random_selector_multiphase_multislice(total_x, func, num_cases):
    """Computes func(x, sel), with sel sampled from [0...num_cases-1].
    主要功能是在执行func的时候传递不同的参数(order)
    Args:
        total_x
        func: Python function to apply.
        num_cases: Python int32, number of cases to sample sel from.

    Returns:
        The result of func(x, sel), where func receives the value of the
        selector as a python integer, but sel is sampled dynamically.
    """
    sel = tf.random_uniform([], maxval=num_cases, dtype=tf.int32)
    # Pass the real x only to one of the func calls.
    return control_flow_ops.merge([
            func(control_flow_ops.switch(total_x, tf.equal(sel, case))[1], case)
            for case in range(num_cases)])[0]



def distort_color_multiphase_multislice(total_x, color_ordering=0, fast_mode=True, scope=None):
    """Distort the color of a Tensor image.

    Each color distortion is non-commutative and thus ordering of the color ops
    matters. Ideally we would randomly permute the ordering of the color ops.
    Rather then adding that level of complication, we select a distinct ordering
    of color ops for each preprocessing thread.

    Args:
        image: 3-D Tensor containing single image in [0, 1].
        color_ordering: Python int, a type of distortion (valid values: 0-3).
        fast_mode: Avoids slower ops (random_hue and random_contrast)
        scope: Optional scope for name_scope.
    Returns:
        3-D Tensor color-distorted image on range [0, 1]
    Raises:
        ValueError: if color_ordering not in [0, 3]
    """
    nc_image = total_x[:, :, 0:3]
    art_image = total_x[:, :, 3:6]
    pv_image = total_x[:, :, 6:9]
    seed = np.random.randint(1, 100)
    with tf.name_scope(scope, 'distort_color', [nc_image, art_image, pv_image]):
        if fast_mode:
            if color_ordering == 0:
                nc_image = tf.image.random_brightness(nc_image, max_delta=32. / 255., seed=seed)
                art_image = tf.image.random_brightness(art_image, max_delta=32. / 255., seed=seed)
                pv_image = tf.image.random_brightness(pv_image, max_delta=32. / 255., seed=seed)
                nc_image = tf.image.random_saturation(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_saturation(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_saturation(pv_image, lower=0.5, upper=1.5, seed=seed)
            else:
                nc_image = tf.image.random_saturation(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_saturation(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_saturation(pv_image, lower=0.5, upper=1.5, seed=seed)
                nc_image = tf.image.random_brightness(nc_image, max_delta=32. / 255., seed=seed)
                art_image = tf.image.random_brightness(art_image, max_delta=32. / 255., seed=seed)
                pv_image = tf.image.random_brightness(pv_image, max_delta=32. / 255., seed=seed)
        else:
            if color_ordering == 0:
                nc_image = tf.image.random_brightness(nc_image, max_delta=32. / 255., seed=seed)
                art_image = tf.image.random_brightness(art_image, max_delta=32. / 255., seed=seed)
                pv_image = tf.image.random_brightness(pv_image, max_delta=32. / 255., seed=seed)

                nc_image = tf.image.random_saturation(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_saturation(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_saturation(pv_image, lower=0.5, upper=1.5, seed=seed)

                nc_image = tf.image.random_hue(nc_image, max_delta=0.2, seed=seed)
                art_image = tf.image.random_hue(art_image, max_delta=0.2, seed=seed)
                pv_image = tf.image.random_hue(pv_image, max_delta=0.2, seed=seed)

                nc_image = tf.image.random_contrast(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_contrast(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_contrast(pv_image, lower=0.5, upper=1.5, seed=seed)
            elif color_ordering == 1:
                nc_image = tf.image.random_saturation(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_saturation(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_saturation(pv_image, lower=0.5, upper=1.5, seed=seed)

                nc_image = tf.image.random_brightness(nc_image, max_delta=32. / 255., seed=seed)
                art_image = tf.image.random_brightness(art_image, max_delta=32. / 255., seed=seed)
                pv_image = tf.image.random_brightness(pv_image, max_delta=32. / 255., seed=seed)

                nc_image = tf.image.random_contrast(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_contrast(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_contrast(pv_image, lower=0.5, upper=1.5, seed=seed)

                nc_image = tf.image.random_hue(nc_image, max_delta=0.2, seed=seed)
                art_image = tf.image.random_hue(art_image, max_delta=0.2, seed=seed)
                pv_image = tf.image.random_hue(pv_image, max_delta=0.2, seed=seed)

            elif color_ordering == 2:
                nc_image = tf.image.random_contrast(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_contrast(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_contrast(pv_image, lower=0.5, upper=1.5, seed=seed)

                nc_image = tf.image.random_hue(nc_image, max_delta=0.2, seed=seed)
                art_image = tf.image.random_hue(art_image, max_delta=0.2, seed=seed)
                pv_image = tf.image.random_hue(pv_image, max_delta=0.2, seed=seed)

                nc_image = tf.image.random_brightness(nc_image, max_delta=32. / 255., seed=seed)
                art_image = tf.image.random_brightness(art_image, max_delta=32. / 255., seed=seed)
                pv_image = tf.image.random_brightness(pv_image, max_delta=32. / 255., seed=seed)

                nc_image = tf.image.random_saturation(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_saturation(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_saturation(pv_image, lower=0.5, upper=1.5, seed=seed)
            elif color_ordering == 3:
                nc_image = tf.image.random_hue(nc_image, max_delta=0.2, seed=seed)
                art_image = tf.image.random_hue(art_image, max_delta=0.2, seed=seed)
                pv_image = tf.image.random_hue(pv_image, max_delta=0.2, seed=seed)

                nc_image = tf.image.random_saturation(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_saturation(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_saturation(pv_image, lower=0.5, upper=1.5, seed=seed)

                nc_image = tf.image.random_contrast(nc_image, lower=0.5, upper=1.5, seed=seed)
                art_image = tf.image.random_contrast(art_image, lower=0.5, upper=1.5, seed=seed)
                pv_image = tf.image.random_contrast(pv_image, lower=0.5, upper=1.5, seed=seed)

                nc_image = tf.image.random_brightness(nc_image, max_delta=32. / 255., seed=seed)
                art_image = tf.image.random_brightness(art_image, max_delta=32. / 255., seed=seed)
                pv_image = tf.image.random_brightness(pv_image, max_delta=32. / 255., seed=seed)
            else:
                raise ValueError('color_ordering must be in [0, 3]')
        # The random_* ops do not necessarily clamp.
        total_output = tf.concat([nc_image, art_image, pv_image], axis=-1)
        return tf.clip_by_value(total_output, 0.0, 1.0)


def distorted_bounding_box_crop_multiphase_multislice_mask(nc_image, art_image, pv_image, mask_image, labels, bboxes, xs,
                                                           ys, min_object_covered, aspect_ratio_range, area_range,
                                                           max_attempts=200,scope=None):
    """Generates cropped_image using a one of the bboxes randomly distorted.

    See `tf.image.sample_distorted_bounding_box` for more documentation.

    Args:
        image: 3-D Tensor of image (it will be converted to floats in [0, 1]).
        bbox: 2-D float Tensor of bounding boxes arranged [num_boxes, coords]
            where each coordinate is [0, 1) and the coordinates are arranged
            as [ymin, xmin, ymax, xmax]. If num_boxes is 0 then it would use the whole
            image.
        min_object_covered: An optional `float`. Defaults to `0.1`. The cropped
            area of the image must contain at least this fraction of any bounding box
            supplied.
        aspect_ratio_range: An optional list of `floats`. The cropped area of the
            image must have an aspect ratio = width / height within this range.
        area_range: An optional list of `floats`. The cropped area of the image
            must contain a fraction of the supplied image within in this range.
        max_attempts: An optional `int`. Number of attempts at generating a cropped
            region of the image of the specified constraints. After `max_attempts`
            failures, return the entire image.
        scope: Optional scope for name_scope.
    Returns:
        A tuple, a 3-D Tensor cropped_image and the distorted bbox
    """
    with tf.name_scope(scope, 'distorted_bounding_box_crop', [nc_image, art_image, pv_image, bboxes, xs, ys]):
        # Each bounding box has shape [1, num_boxes, box coords] and
        # the coordinates are ordered [ymin, xmin, ymax, xmax].
        num_bboxes = tf.shape(bboxes)[0]

        def has_bboxes():
            return bboxes, labels, xs, ys

        def no_bboxes():
            xmin = tf.random_uniform((1, 1), minval=0, maxval=0.9)
            ymin = tf.random_uniform((1, 1), minval=0, maxval=0.9)
            w = tf.constant(0.1, dtype=tf.float32)
            h = w
            xmax = xmin + w
            ymax = ymin + h
            rnd_bboxes = tf.concat([ymin, xmin, ymax, xmax], axis=1)
            rnd_labels = tf.constant([config.background_label], dtype=tf.int64)
            rnd_xs = tf.concat([xmin, xmax, xmax, xmin], axis=1)
            rnd_ys = tf.concat([ymin, ymin, ymax, ymax], axis=1)

            return rnd_bboxes, rnd_labels, rnd_xs, rnd_ys

        bboxes, labels, xs, ys = tf.cond(num_bboxes > 0, has_bboxes, no_bboxes)
        bbox_begin, bbox_size, distort_bbox = tf.image.sample_distorted_bounding_box(
            tf.shape(pv_image),
            bounding_boxes=tf.expand_dims(bboxes, 0),
            min_object_covered=min_object_covered,
            aspect_ratio_range=aspect_ratio_range,
            area_range=area_range,
            max_attempts=max_attempts,
            use_image_if_no_bounding_boxes=True)
        distort_bbox = distort_bbox[0, 0]

        # Crop the image to the specified bounding box.
        nc_cropped_image = tf.slice(nc_image, bbox_begin, bbox_size)
        art_cropped_image = tf.slice(art_image, bbox_begin, bbox_size)
        pv_cropped_image = tf.slice(pv_image, bbox_begin, bbox_size)
        mask_cropped_image = tf.slice(mask_image, bbox_begin, bbox_size)
        # Restore the shape since the dynamic slice loses 3rd dimension.
        nc_cropped_image.set_shape([None, None, 3])
        art_cropped_image.set_shape([None, None, 3])
        pv_cropped_image.set_shape([None, None, 3])
        mask_cropped_image.set_shape([None, None, 1])

        # Update bounding boxes: resize and filter out.
        bboxes, xs, ys = tfe.bboxes_resize(distort_bbox, bboxes, xs, ys)
        labels, bboxes, xs, ys = tfe.bboxes_filter_overlap(labels, bboxes, xs, ys,
                                                           threshold=BBOX_CROP_OVERLAP, assign_value=LABEL_IGNORE)
        return nc_cropped_image, art_cropped_image, pv_cropped_image, mask_cropped_image, labels, bboxes, xs, ys, \
               distort_bbox


def tf_rotate_image(image, xs, ys):
    image, bboxes, xs, ys = tf.py_func(rotate_image, [image, xs, ys], [tf.uint8, tf.float32, tf.float32, tf.float32])
    image.set_shape([None, None, 3])
    bboxes.set_shape([None, 4])
    xs.set_shape([None, 4])
    ys.set_shape([None, 4])
    return image, bboxes, xs, ys


def rotate_image(image, xs, ys):
    rotation_angle = np.random.randint(low = -90, high = 90);
    scale = np.random.uniform(low = MIN_ROTATION_SCLAE, high = MAX_ROTATION_SCLAE)
#     scale = 1.0
    h, w = image.shape[0:2]
    # rotate image
    image, M = util.img.rotate_about_center(image, rotation_angle, scale = scale)
    
    nh, nw = image.shape[0:2]
    
    # rotate bboxes
    xs = xs * w
    ys = ys * h
    def rotate_xys(xs, ys):
        xs = np.reshape(xs, -1)
        ys = np.reshape(ys, -1)
        xs, ys = np.dot(M, np.transpose([xs, ys, 1]))
        xs = np.reshape(xs, (-1, 4))
        ys = np.reshape(ys, (-1, 4))
        return xs, ys
    xs, ys = rotate_xys(xs, ys)
    xs = xs * 1.0 / nw
    ys = ys * 1.0 / nh
    xmin = np.min(xs, axis = 1)
    xmin[np.where(xmin < 0)] = 0    
    
    xmax = np.max(xs, axis = 1)
    xmax[np.where(xmax > 1)] = 1
    
    ymin = np.min(ys, axis = 1)
    ymin[np.where(ymin < 0)] = 0
    
    ymax = np.max(ys, axis = 1)
    ymax[np.where(ymax > 1)] = 1
    
    bboxes = np.transpose(np.asarray([ymin, xmin, ymax, xmax]))
    image = np.asarray(image, np.uint8)
    return image, bboxes, xs, ys
 

def preprocess_for_train_multiphase_multislice_mask(nc_image, art_image, pv_image, mask_image, labels, bboxes, xs, ys,
                         out_shape, data_format='NHWC',
                         scope='ssd_preprocessing_train'):
    """Preprocesses the given image for training.

    Note that the actual resizing scale is sampled from
        [`resize_size_min`, `resize_size_max`].

    Args:
        image: A `Tensor` representing an image of arbitrary size.
        output_height: The height of the image after preprocessing.
        output_width: The width of the image after preprocessing.
        resize_side_min: The lower bound for the smallest side of the image for
            aspect-preserving resizing.
        resize_side_max: The upper bound for the smallest side of the image for
            aspect-preserving resizing.

    Returns:
        A preprocessed image.
    """
    fast_mode = False
    with tf.name_scope(scope, 'ssd_preprocessing_train', [nc_image, art_image, pv_image, labels, bboxes]):
        if nc_image.get_shape().ndims != 3:
            raise ValueError('Input must be of size [height, width, C>0]')
        if art_image.get_shape().ndims != 3:
            raise ValueError('Input must be of size [height, width, C>0]')
        if pv_image.get_shape().ndims != 3:
            raise ValueError('Input must be of size [height, width, C>0]')
        # rotate image by 0, 0.5 * pi, pi, 1.5 * pi randomly
        #         if USE_ROTATION:
        #             image, bboxes, xs, ys = tf_image.random_rotate90(image, bboxes, xs, ys)
        # rotate image by 0, 0.5 * pi, pi, 1.5 * pi randomly
        # 旋转
        if USE_ROTATION:
            rnd = tf.random_uniform((), minval=0, maxval=1)

            def rotate():
                # return tf_image.random_rotate90(nc_image, bboxes, xs, ys),
                return tf_image_multiphase_multislice_mask.random_rotate90_multiphase_multislice_mask(nc_image,
                                                                                                      art_image,
                                                                                                      pv_image,
                                                                                                      mask_image,
                                                                                                      bboxes, xs, ys)

            def no_rotate():
                return nc_image, art_image, pv_image, mask_image, bboxes, xs, ys

            nc_image, art_image, pv_image, mask_image, bboxes, xs, ys = tf.cond(tf.less(rnd, config.rotation_prob),
                                                                                rotate,
                                                                                no_rotate)

        # expand image
        # 通过pad或者是crop的方式来做resize
        if MAX_EXPAND_SCALE > 1:
            rnd2 = tf.random_uniform((), minval=0, maxval=1)

            def expand():
                scale = tf.random_uniform([], minval=1.0,
                                          maxval=MAX_EXPAND_SCALE, dtype=tf.float32)
                image_shape = tf.cast(tf.shape(nc_image), dtype=tf.float32)
                image_h, image_w = image_shape[0], image_shape[1]
                target_h = tf.cast(image_h * scale, dtype=tf.int32)
                target_w = tf.cast(image_w * scale, dtype=tf.int32)
                tf.logging.info('expanded')
                return tf_image_multiphase_multislice_mask.resize_image_bboxes_with_crop_or_pad_multiphase_multislice_mask(
                    nc_image, art_image, pv_image, mask_image, bboxes, xs, ys, target_h, target_w)

            def no_expand():
                return nc_image, art_image, pv_image, mask_image, bboxes, xs, ys

            nc_image, art_image, pv_image, mask_image, bboxes, xs, ys = tf.cond(tf.less(rnd2, config.expand_prob),
                                                                                expand, no_expand)

        # Convert to float scaled [0, 1].
        if nc_image.dtype != tf.float32:
            nc_image = tf.image.convert_image_dtype(nc_image, dtype=tf.float32)
        if art_image.dtype != tf.float32:
            art_image = tf.image.convert_image_dtype(art_image, dtype=tf.float32)
        if pv_image.dtype != tf.float32:
            pv_image = tf.image.convert_image_dtype(pv_image, dtype=tf.float32)
        if mask_image.dtype != tf.uint8:
            mask_image = tf.image.convert_image_dtype(mask_image, dtype=tf.uint8)


        # Distort image and bounding boxes.
        nc_dst_image, art_dst_image, pv_dst_image, mask_dst_image, labels, bboxes, xs, ys, distort_bbox = \
            distorted_bounding_box_crop_multiphase_multislice_mask(nc_image, art_image, pv_image, mask_image, labels, bboxes, xs, ys,
                                                              min_object_covered=MIN_OBJECT_COVERED,
                                                              aspect_ratio_range=CROP_ASPECT_RATIO_RANGE,
                                                              area_range=AREA_RANGE)
        # Resize image to output size.
        nc_dst_image = tf_image_multiphase_multislice_mask.resize_image(nc_dst_image, out_shape,
                                          method=tf.image.ResizeMethod.BILINEAR,
                                          align_corners=False)
        tf_summary_image(nc_dst_image, bboxes, 'image_shape_distorted/nc')

        art_dst_image = tf_image_multiphase_multislice_mask.resize_image(art_dst_image, out_shape,
                                              method=tf.image.ResizeMethod.BILINEAR,
                                              align_corners=False)
        tf_summary_image(art_dst_image, bboxes, 'image_shape_distorted/art')

        pv_dst_image = tf_image_multiphase_multislice_mask.resize_image(pv_dst_image, out_shape,
                                             method=tf.image.ResizeMethod.BILINEAR,
                                             align_corners=False)
        tf_summary_image(pv_dst_image, bboxes, 'image_shape_distorted/pv')

        mask_dst_image = tf_image_multiphase_multislice_mask.resize_image(mask_dst_image, out_shape,
                                                                        method=tf.image.ResizeMethod.NEAREST_NEIGHBOR,
                                                                        align_corners=True)

        tf_summary_image(tf.image.convert_image_dtype(mask_dst_image, tf.float32), bboxes, 'image_shape_distorted/mask')

        # Filter bboxes using the length of shorter sides
        if USING_SHORTER_SIDE_FILTERING:
            xs = xs * out_shape[1]
            ys = ys * out_shape[0]
            labels, bboxes, xs, ys = tfe.bboxes_filter_by_shorter_side(labels,
                                                                       bboxes, xs, ys,
                                                                       min_height=MIN_SHORTER_SIDE,
                                                                       max_height=MAX_SHORTER_SIDE,
                                                                       assign_value=LABEL_IGNORE)
            xs = xs / out_shape[1]
            ys = ys / out_shape[0]

        # Randomly distort the colors. There are 4 ways to do it.
        total_image = tf.concat([nc_dst_image, art_dst_image, pv_dst_image], axis=-1)
        total_image_output = apply_with_random_selector_multiphase_multislice(
            total_image,
            lambda total_x, ordering: distort_color_multiphase_multislice(total_x, ordering, fast_mode),
            num_cases=4)
        nc_dst_image = total_image_output[:, :, 0:3]
        art_dst_image = total_image_output[:, :, 3:6]
        pv_dst_image = total_image_output[:, :, 6:9]

        tf_summary_image(nc_dst_image, bboxes, 'image_color_distorted/nc')
        tf_summary_image(art_dst_image, bboxes, 'image_color_distorted/art')
        tf_summary_image(pv_dst_image, bboxes, 'image_color_distorted/pv')
        tf_summary_image(tf.image.convert_image_dtype(mask_dst_image * 50, tf.float32), bboxes, 'image_color_distorted/mask')

        # Rescale to VGG input scale.
        nc_dst_image = nc_dst_image * 255.
        nc_dst_image = tf_image_whitened(nc_dst_image, [_R_MEAN, _G_MEAN, _B_MEAN])

        art_dst_image = art_dst_image * 255.
        art_dst_image = tf_image_whitened(art_dst_image, [_R_MEAN, _G_MEAN, _B_MEAN])

        pv_dst_image = pv_dst_image * 255.
        pv_dst_image = tf_image_whitened(pv_dst_image, [_R_MEAN, _G_MEAN, _B_MEAN])

        # Image data format.
        if data_format == 'NCHW':
            nc_dst_image = tf.transpose(nc_dst_image, perm=(2, 0, 1))
            art_dst_image = tf.transpose(art_dst_image, perm=(2, 0, 1))
            pv_dst_image = tf.transpose(pv_dst_image, perm=(2, 0, 1))
        return nc_dst_image, art_dst_image, pv_dst_image, mask_dst_image, labels, bboxes, xs, ys


def preprocess_for_eval_multiphase_multislice_mask(nc_image, art_image, pv_image, labels, bboxes, xs, ys,
                        out_shape, data_format='NHWC',
                        resize=Resize.WARP_RESIZE,
                        do_resize=True,
                        scope='ssd_preprocessing_train'):
    """Preprocess an image for evaluation.

    Args:
        image: A `Tensor` representing an image of arbitrary size.
        out_shape: Output shape after pre-processing (if resize != None)
        resize: Resize strategy.

    Returns:
        A preprocessed image.
    """
    with tf.name_scope(scope):
        if nc_image.get_shape().ndims != 3:
            raise ValueError('Input must be of size [height, width, C>0]')
        if art_image.get_shape().ndims != 3:
            raise ValueError('Input must be of size [height, width, C>0]')
        if pv_image.get_shape().ndims != 3:
            raise ValueError('Input must be of size [height, width, C>0]')

        nc_image = tf.to_float(nc_image)
        nc_image = tf_image_whitened(nc_image, [_R_MEAN, _G_MEAN, _B_MEAN])

        art_image = tf.to_float(art_image)
        art_image = tf_image_whitened(art_image, [_R_MEAN, _G_MEAN, _B_MEAN])

        pv_image = tf.to_float(pv_image)
        pv_image = tf_image_whitened(pv_image, [_R_MEAN, _G_MEAN, _B_MEAN])

        if do_resize:
            if resize == Resize.NONE:
                pass
            else:
                nc_image = tf_image_multiphase_multislice_mask.resize_image(nc_image, out_shape,
                                                                            method=tf.image.ResizeMethod.BILINEAR,
                                                                            align_corners=False)
                art_image = tf_image_multiphase_multislice_mask.resize_image(art_image, out_shape,
                                                                             method=tf.image.ResizeMethod.BILINEAR,
                                                                             align_corners=False)
                pv_image = tf_image_multiphase_multislice_mask.resize_image(pv_image, out_shape,
                                                                            method=tf.image.ResizeMethod.BILINEAR,
                                                                            align_corners=False)

        # Image data format.
        if data_format == 'NCHW':
            nc_image = tf.transpose(nc_image, perm=(2, 0, 1))
            art_image = tf.transpose(art_image, perm=(2, 0, 1))
            pv_image = tf.transpose(pv_image, perm=(2, 0, 1))
        return nc_image, art_image, pv_image, labels, bboxes, xs, ys

def preprocess_image_multiphase_multislice_mask(nc_image, art_image, pv_image, mask_image,
                     labels = None,
                     bboxes = None,
                     xs = None, ys = None,
                     out_shape = None,
                     data_format = 'NHWC',
                     is_training=False,
                     **kwargs):
    """Pre-process an given image.

    Args:
      image: A `Tensor` representing an image of arbitrary size.
      output_height: The height of the image after preprocessing.
      output_width: The width of the image after preprocessing.
      is_training: `True` if we're preprocessing the image for training and
        `False` otherwise.
      resize_side_min: The lower bound for the smallest side of the image for
        aspect-preserving resizing. If `is_training` is `False`, then this value
        is used for rescaling.
      resize_side_max: The upper bound for the smallest side of the image for
        aspect-preserving resizing. If `is_training` is `False`, this value is
         ignored. Otherwise, the resize side is sampled from
         [resize_size_min, resize_size_max].

    Returns:
      A preprocessed image.
    """
    if is_training:
        return preprocess_for_train_multiphase_multislice_mask(nc_image, art_image, pv_image, mask_image, labels, bboxes, xs, ys,
                                    out_shape=out_shape,
                                    data_format=data_format)
    else:
        return preprocess_for_eval_multiphase_multislice_mask(nc_image, art_image, pv_image, labels, bboxes, xs, ys,
                                   out_shape=out_shape,
                                   data_format=data_format,
                                   **kwargs)

