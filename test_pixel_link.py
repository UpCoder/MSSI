# -*- coding=utf-8 -*-

import numpy as np
import math
import tensorflow as tf
from tensorflow.python.ops import control_flow_ops
from tensorflow.contrib.training.python.training import evaluation
from datasets import dataset_factory
from preprocessing import ssd_vgg_preprocessing
from tf_extended import metrics as tfe_metrics
import util
import cv2
import pixel_link
from nets import pixel_link_symbol
from glob import glob
import os


slim = tf.contrib.slim
import config
# =========================================================================== #
# Checkpoint and running Flags
# =========================================================================== #
tf.app.flags.DEFINE_string('checkpoint_path', None, 
   'the path of pretrained model to be used. If there are checkpoints\
    in train_dir, this config will be ignored.')
tf.app.flags.DEFINE_string('pred_path', None, 'save the pred path, it only save top left and bottom right')
tf.app.flags.DEFINE_string('score_map_path', None, 'save the score map path')
tf.app.flags.DEFINE_string('seg_map_path', None, 'save the segmentation map path')
tf.app.flags.DEFINE_float('gpu_memory_fraction', -1, 
  'the gpu memory fraction to be used. If less than 0, allow_growth = True is used.')


# =========================================================================== #
# I/O and preprocessing Flags.
# =========================================================================== #
tf.app.flags.DEFINE_integer(
    'num_readers', 1,
    'The number of parallel readers that read data from the dataset.')
tf.app.flags.DEFINE_integer(
    'num_preprocessing_threads', 4,
    'The number of threads used to create the batches.')
tf.app.flags.DEFINE_bool('preprocessing_use_rotation', False, 
             'Whether to use rotation for data augmentation')

# =========================================================================== #
# Dataset Flags.
# =========================================================================== #
tf.app.flags.DEFINE_string(
    'dataset_name', 'icdar2015', 'The name of the dataset to load.')
tf.app.flags.DEFINE_string(
    'dataset_split_name', 'test', 'The name of the train/test split.')
tf.app.flags.DEFINE_string('dataset_dir', 
           util.io.get_absolute_path('~/dataset/ICDAR2015/Challenge4/ch4_test_images'), 
           'The directory where the dataset files are stored.')

tf.app.flags.DEFINE_integer('eval_image_width', 1280, 'Train image size')
tf.app.flags.DEFINE_integer('eval_image_height', 768, 'Train image size')
tf.app.flags.DEFINE_bool('using_moving_average', True, 
                         'Whether to use ExponentionalMovingAverage')
tf.app.flags.DEFINE_float('moving_average_decay', 0.9999, 
                          'The decay rate of ExponentionalMovingAverage')

tf.app.flags.DEFINE_bool('multiphase_multislice_flag', False, 'the data whether is multiphase and multislice')
tf.app.flags.DEFINE_bool('clstm_flag', False, 'the network whether use the clstm block')
tf.app.flags.DEFINE_bool('mask_flag', False, 'the dataset whether use the mask')
tf.app.flags.DEFINE_bool('multiscale_flag', False, 'the dataset whether use the mask')
tf.app.flags.DEFINE_string('config_path', '/home/give/github/pixel_link', 'the default path of config path')
FLAGS = tf.app.flags.FLAGS

def config_initialization():
    # image shape and feature layers shape inference
    image_shape = (FLAGS.eval_image_height, FLAGS.eval_image_width)
    
    if not FLAGS.dataset_dir:
        raise ValueError('You must supply the dataset directory with --dataset_dir')
    
    tf.logging.set_verbosity(tf.logging.DEBUG)
    config.load_config(FLAGS.config_path)
    # config.load_config(FLAGS.checkpoint_path)
    # config.load_config(FLAGS.pred_path)
    # config.load_config(FLAGS.score_map_path)
    # config.load_config(FLAGS.multiphase_multislice_flag)
    config.init_config(image_shape, 
                       batch_size = 1, 
                       pixel_conf_threshold = 0.8,
                       link_conf_threshold = 0.8,
                       num_gpus = 1,
                       # multiphase_multislice_flag=False
                   )
    
    util.proc.set_proc_name('test_pixel_link_on'+ '_' + FLAGS.dataset_name)
    


def to_txt(txt_path, image_name, 
           image_data, pixel_pos_scores, link_pos_scores):
    # write detection result as txt files
    def write_result_as_pred_txt(image_name, bboxes, bboxes_score):
        filename = util.io.join_path(FLAGS.pred_path, '%s.txt' % image_name)
        lines = []
        for b_idx, (bbox, bbox_score) in enumerate(zip(bboxes, bboxes_score)):
            min_x = np.min([bbox[0], bbox[2], bbox[4], bbox[6]])
            max_x = np.max([bbox[0], bbox[2], bbox[4], bbox[6]])
            min_y = np.min([bbox[1], bbox[3], bbox[5], bbox[7]])
            max_y = np.max([bbox[1], bbox[3], bbox[5], bbox[7]])
            lines.append('CYST %.4f %d %d %d %d\n' % (bbox_score, min_x, min_y, max_x, max_y))
        util.io.write_lines(filename, lines)
        print('result has been written to: ', filename)

    def write_result_as_txt(image_name, bboxes, path):
        filename = util.io.join_path(path, 'res_%s.txt'%(image_name))
        lines = []
        pred_lines = []
        for b_idx, bbox in enumerate(bboxes):
              values = [int(v) for v in bbox]
              line = "%d, %d, %d, %d, %d, %d, %d, %d\n"%tuple(values)
              lines.append(line)

        util.io.write_lines(filename, lines)
        print('result has been written to:', filename)
    # 其实只有一个image, [1, W, H, C]
    print('the shape of pixel_pos_scores is ', np.shape(pixel_pos_scores), np.min(pixel_pos_scores),
          np.max(pixel_pos_scores))
    mask = pixel_link.decode_batch(pixel_pos_scores, link_pos_scores)[0, ...]
    bboxes, bboxes_score, pixel_pos_scores = pixel_link.mask_to_bboxes(mask, pixel_pos_scores, image_data.shape)
    print('the shape of pixel_pos_scores is ', np.shape(pixel_pos_scores), np.min(pixel_pos_scores),
          np.max(pixel_pos_scores))
    score_map_path = util.io.join_path(FLAGS.score_map_path, '%s.jpg'%image_name)
    cv2.imwrite(score_map_path, np.asarray(pixel_pos_scores * 255, np.uint8))
    print('score will be written in ', score_map_path)
    write_result_as_txt(image_name, bboxes, txt_path)
    write_result_as_pred_txt(image_name, bboxes, bboxes_score)


def to_txt_mask(txt_path, image_name,
           image_data, pixel_pos_scores, link_pos_scores, pixel_seg_score):
    # write detection result as txt files
    def write_result_as_pred_txt(image_name, bboxes, bboxes_score, pixel_wise_category):
        from config import pixel2type
        def compute_the_category(pixel_wise_category, min_x, max_x, min_y, max_y, class_num=5):
            pixel_num = []
            cropped = pixel_wise_category[min_y: max_y, min_x: max_x]
            for i in range(1, class_num + 1):
                pixel_num.append(np.sum(cropped == i))
            pixel_wise_category_cp = np.copy(pixel_wise_category * 100)
            points = np.asarray([[min_x, min_y], [min_x, max_y], [max_x, max_y], [max_x, min_y]], np.int32)
            cnts = util.img.points_to_contours(points)
            util.img.draw_contours(pixel_wise_category_cp,
                                   cnts, -1,
                                   color=255, border_width=1)
            cv2.imwrite('/home/give/Desktop/test.png', pixel_wise_category_cp)
            if np.sum(pixel_num) == 0:
                return 2



            return np.argmax(pixel_num) + 1

        filename = util.io.join_path(FLAGS.pred_path, '%s.txt' % image_name)
        lines = []
        for b_idx, (bbox, bbox_score) in enumerate(zip(bboxes, bboxes_score)):
            min_x = np.min([bbox[0], bbox[2], bbox[4], bbox[6]])
            max_x = np.max([bbox[0], bbox[2], bbox[4], bbox[6]])
            min_y = np.min([bbox[1], bbox[3], bbox[5], bbox[7]])
            max_y = np.max([bbox[1], bbox[3], bbox[5], bbox[7]])
            label_idx = compute_the_category(pixel_wise_category, min_x, max_x, min_y, max_y)
            label_name = pixel2type[label_idx * 50]
            lines.append('%s %.4f %d %d %d %d\n' % (label_name, bbox_score, min_x, min_y, max_x, max_y))
        util.io.write_lines(filename, lines)
        print('result has been written to: ', filename)

    def write_result_as_txt(image_name, bboxes, path):
        filename = util.io.join_path(path, 'res_%s.txt'%(image_name))
        lines = []
        pred_lines = []
        for b_idx, bbox in enumerate(bboxes):
              values = [int(v) for v in bbox]
              line = "%d, %d, %d, %d, %d, %d, %d, %d\n"%tuple(values)
              lines.append(line)

        util.io.write_lines(filename, lines)
        print('result has been written to:', filename)

    # pixel_seg_score = util.img.resize(img=pixel_seg_score[0], size=image_data.shape[:2])
    pixel_wise_category = np.argmax(pixel_seg_score[0], axis=-1)
    pixel_wise_category = util.img.resize(pixel_wise_category, size=image_data.shape[:2],
                                          interpolation=cv2.INTER_NEAREST)
    # 其实只有一个image, [1, W, H, C]

    mask = pixel_link.decode_batch(pixel_pos_scores, link_pos_scores)[0, ...]
    bboxes, bboxes_score, pixel_pos_scores = pixel_link.mask_to_bboxes(mask, pixel_pos_scores, image_data.shape)

    print('the shape of pixel_pos_scores is ', np.shape(pixel_pos_scores), np.min(pixel_pos_scores),
          np.max(pixel_pos_scores))
    print('the shape of pixel_wise_category is ', np.shape(pixel_wise_category), np.min(pixel_wise_category),
          np.max(pixel_wise_category))
    score_map_path = util.io.join_path(FLAGS.score_map_path, '%s.jpg'%image_name)
    seg_map_path = util.io.join_path(FLAGS.seg_map_path, '%s.png' % image_name)
    cv2.imwrite(score_map_path, np.asarray(pixel_pos_scores * 255, np.uint8))
    cv2.imwrite(seg_map_path, np.asarray(pixel_wise_category * 50, np.uint8))
    print('score will be written in ', score_map_path)
    write_result_as_txt(image_name, bboxes, txt_path)
    write_result_as_pred_txt(image_name, bboxes, bboxes_score, pixel_wise_category)


def test_multiphase_multislice_clstm_mask():
    from preprocessing import ssd_vgg_preprocessing_multiphase_multislice_mask
    with tf.name_scope('test'):
        nc_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        art_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        pv_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        mask_image = tf.placeholder(dtype=tf.uint8, shape=[None, None, 1])
        image_shape = tf.placeholder(dtype=tf.int32, shape=[3, ])
        nc_processed_image, art_processed_image, pv_processed_image, _, _, _, _ = \
            ssd_vgg_preprocessing_multiphase_multislice_mask.preprocess_image_multiphase_multislice_mask(
            nc_image, art_image, pv_image, mask_image, None, None, None, None,
            out_shape=config.image_shape,
            data_format=config.data_format,
            is_training=False)
        b_nc_image = tf.expand_dims(nc_processed_image, axis=0)
        b_art_image = tf.expand_dims(art_processed_image, axis=0)
        b_pv_image = tf.expand_dims(pv_processed_image, axis=0)
        # b_mask_image = tf.expand_dims(mask_preprocessed_image, axis=0)
        if not FLAGS.multiscale_flag:
            net = pixel_link_symbol.PixelLinkNet_multiphase_multislice_clstm_mask(b_nc_image, b_art_image, b_pv_image,
                                                                                  None, is_training=False,
                                                                                 batch_size_ph=1)
        else:
            print('is training is False')
            net = pixel_link_symbol.PixelLinkNet_multiphase_multislice_clstm_mask_multiscale(b_nc_image, b_art_image,
                                                                                             b_pv_image,
                                                                                             None,
                                                                                             is_training=False,
                                                                                             batch_size_ph=1)
        global_step = slim.get_or_create_global_step()


    sess_config = tf.ConfigProto(log_device_placement=False, allow_soft_placement=True)
    if FLAGS.gpu_memory_fraction < 0:
        sess_config.gpu_options.allow_growth = True
    elif FLAGS.gpu_memory_fraction > 0:
        sess_config.gpu_options.per_process_gpu_memory_fraction = FLAGS.gpu_memory_fraction

    checkpoint_dir = util.io.get_dir(FLAGS.checkpoint_path)
    logdir = util.io.join_path(checkpoint_dir, 'test', FLAGS.dataset_name + '_' + FLAGS.dataset_split_name)

    # Variables to restore: moving avg. or normal weights.
    if FLAGS.using_moving_average:
        variable_averages = tf.train.ExponentialMovingAverage(
            FLAGS.moving_average_decay)
        variables_to_restore = variable_averages.variables_to_restore()
        variables_to_restore[global_step.op.name] = global_step
    else:
        variables_to_restore = slim.get_variables_to_restore()

    saver = tf.train.Saver(var_list=variables_to_restore)

    # image_names = util.io.ls(FLAGS.dataset_dir)
    # image_names.sort()
    image_names = glob(util.io.join_path(FLAGS.dataset_dir, '*_ART.PNG'))
    image_names = [os.path.basename(image_name) for image_name in image_names]
    image_names = [image_name[:-8] for image_name in image_names]
    image_names.sort()
    checkpoint = FLAGS.checkpoint_path
    checkpoint_name = util.io.get_filename(str(checkpoint))
    dump_path = util.io.join_path(logdir, checkpoint_name)
    txt_path = util.io.join_path(dump_path, 'txt')
    zip_path = util.io.join_path(dump_path, checkpoint_name + '_det.zip')

    with tf.Session(config=sess_config) as sess:
        saver.restore(sess, checkpoint)

        for iter, image_name in enumerate(image_names):
            nc_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_NNC.PNG'), rgb=True)
            art_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_ART.PNG'), rgb=True)
            pv_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_PPV.PNG'), rgb=True)

            image_name = image_name.split('.')[0]
            pixel_pos_scores, link_pos_scores, pixel_seg_score = sess.run(
                [net.pixel_pos_scores, net.link_pos_scores, net.pixel_seg_score],
                feed_dict={
                    nc_image: nc_image_data,
                    art_image: art_image_data,
                    pv_image: pv_image_data
                })

            print('%d/%d: %s' % (iter + 1, len(image_names), image_name))
            to_txt_mask(txt_path,
                   image_name, pv_image_data,
                   pixel_pos_scores, link_pos_scores, pixel_seg_score)

    # create zip file for icdar2015
    cmd = 'cd %s;zip -j %s %s/*' % (dump_path, zip_path, txt_path)

    print(cmd)
    util.cmd.cmd(cmd)
    print("zip file created: ", util.io.join_path(dump_path, zip_path))


def test_multiphase_multislice_clstm():
    from preprocessing import ssd_vgg_preprocessing_multiphase_multislice
    with tf.name_scope('test'):
        nc_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        art_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        pv_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        image_shape = tf.placeholder(dtype=tf.int32, shape=[3, ])
        nc_processed_image, art_processed_image, pv_processed_image, _, _, _, _ = ssd_vgg_preprocessing_multiphase_multislice.preprocess_image_multiphase_multislice(
            nc_image, art_image, pv_image, None, None, None, None,
            out_shape=config.image_shape,
            data_format=config.data_format,
            is_training=False)
        b_nc_image = tf.expand_dims(nc_processed_image, axis=0)
        b_art_image = tf.expand_dims(art_processed_image, axis=0)
        b_pv_image = tf.expand_dims(pv_processed_image, axis=0)


        net = pixel_link_symbol.PixelLinkNet_multiphase_multislice_clstm(b_nc_image, b_art_image, b_pv_image,
                                                                         is_training=True, batch_size_ph=1, multiscale_flag=FLAGS.multiscale_flag)
        global_step = slim.get_or_create_global_step()

    sess_config = tf.ConfigProto(log_device_placement=False, allow_soft_placement=True)
    if FLAGS.gpu_memory_fraction < 0:
        sess_config.gpu_options.allow_growth = True
    elif FLAGS.gpu_memory_fraction > 0:
        sess_config.gpu_options.per_process_gpu_memory_fraction = FLAGS.gpu_memory_fraction

    checkpoint_dir = util.io.get_dir(FLAGS.checkpoint_path)
    logdir = util.io.join_path(checkpoint_dir, 'test', FLAGS.dataset_name + '_' + FLAGS.dataset_split_name)

    # Variables to restore: moving avg. or normal weights.
    if FLAGS.using_moving_average:
        variable_averages = tf.train.ExponentialMovingAverage(
            FLAGS.moving_average_decay)
        variables_to_restore = variable_averages.variables_to_restore()
        variables_to_restore[global_step.op.name] = global_step
    else:
        variables_to_restore = slim.get_variables_to_restore()

    saver = tf.train.Saver(var_list=variables_to_restore)

    # image_names = util.io.ls(FLAGS.dataset_dir)
    # image_names.sort()
    image_names = glob(util.io.join_path(FLAGS.dataset_dir, '*_ART.jpg'))
    image_names = [os.path.basename(image_name) for image_name in image_names]
    image_names = [image_name[:-8] for image_name in image_names]
    image_names.sort()
    checkpoint = FLAGS.checkpoint_path
    checkpoint_name = util.io.get_filename(str(checkpoint))
    dump_path = util.io.join_path(logdir, checkpoint_name)
    txt_path = util.io.join_path(dump_path, 'txt')
    zip_path = util.io.join_path(dump_path, checkpoint_name + '_det.zip')

    with tf.Session(config=sess_config) as sess:
        saver.restore(sess, checkpoint)

        for iter, image_name in enumerate(image_names):
            nc_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_NNC.jpg'), rgb=True)
            art_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_ART.jpg'), rgb=True)
            pv_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_PPV.jpg'), rgb=True)
            image_name = image_name.split('.')[0]
            pixel_pos_scores, link_pos_scores = sess.run(
                [net.pixel_pos_scores, net.link_pos_scores],
                feed_dict={
                    nc_image: nc_image_data,
                    art_image: art_image_data,
                    pv_image: pv_image_data
                })

            print('%d/%d: %s' % (iter + 1, len(image_names), image_name))
            to_txt(txt_path,
                   image_name, pv_image_data,
                   pixel_pos_scores, link_pos_scores)

    # create zip file for icdar2015
    cmd = 'cd %s;zip -j %s %s/*' % (dump_path, zip_path, txt_path)

    print(cmd)
    util.cmd.cmd(cmd)
    print("zip file created: ", util.io.join_path(dump_path, zip_path))



def test_multiphase_multislice():
    from preprocessing import ssd_vgg_preprocessing_multiphase_multislice
    with tf.name_scope('test'):
        nc_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        art_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        pv_image = tf.placeholder(dtype=tf.int32, shape=[None, None, 3])
        image_shape = tf.placeholder(dtype=tf.int32, shape=[3, ])
        nc_processed_image, art_processed_image, pv_processed_image, _, _, _, _ = ssd_vgg_preprocessing_multiphase_multislice.preprocess_image_multiphase_multislice(
            nc_image, art_image, pv_image, None, None, None, None,
            out_shape=config.image_shape,
            data_format=config.data_format,
            is_training=False)
        b_nc_image = tf.expand_dims(nc_processed_image, axis=0)
        b_art_image = tf.expand_dims(art_processed_image, axis=0)
        b_pv_image = tf.expand_dims(pv_processed_image, axis=0)

        net = pixel_link_symbol.PixelLinkNet_multiphase_multislice(b_nc_image, b_art_image, b_pv_image, is_training=True, multiscale_flag=FLAGS.multiscale_flag)
        global_step = slim.get_or_create_global_step()

    sess_config = tf.ConfigProto(log_device_placement=False, allow_soft_placement=True)
    if FLAGS.gpu_memory_fraction < 0:
        sess_config.gpu_options.allow_growth = True
    elif FLAGS.gpu_memory_fraction > 0:
        sess_config.gpu_options.per_process_gpu_memory_fraction = FLAGS.gpu_memory_fraction

    checkpoint_dir = util.io.get_dir(FLAGS.checkpoint_path)
    logdir = util.io.join_path(checkpoint_dir, 'test', FLAGS.dataset_name + '_' + FLAGS.dataset_split_name)

    # Variables to restore: moving avg. or normal weights.
    if FLAGS.using_moving_average:
        variable_averages = tf.train.ExponentialMovingAverage(
            FLAGS.moving_average_decay)
        variables_to_restore = variable_averages.variables_to_restore()
        variables_to_restore[global_step.op.name] = global_step
    else:
        variables_to_restore = slim.get_variables_to_restore()

    saver = tf.train.Saver(var_list=variables_to_restore)

    # image_names = util.io.ls(FLAGS.dataset_dir)
    # image_names.sort()
    image_names = glob(util.io.join_path(FLAGS.dataset_dir, '*_ART.jpg'))
    image_names = [os.path.basename(image_name) for image_name in image_names]
    image_names = [image_name[:-8] for image_name in image_names]
    image_names.sort()
    checkpoint = FLAGS.checkpoint_path
    checkpoint_name = util.io.get_filename(str(checkpoint))
    dump_path = util.io.join_path(logdir, checkpoint_name)
    txt_path = util.io.join_path(dump_path, 'txt')
    zip_path = util.io.join_path(dump_path, checkpoint_name + '_det.zip')

    with tf.Session(config=sess_config) as sess:
        saver.restore(sess, checkpoint)

        for iter, image_name in enumerate(image_names):
            nc_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_NNC.jpg'), rgb=True)
            art_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_ART.jpg'), rgb=True)
            pv_image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name + '_PPV.jpg'), rgb=True)
            image_name = image_name.split('.')[0]
            pixel_pos_scores, link_pos_scores = sess.run(
                [net.pixel_pos_scores, net.link_pos_scores],
                feed_dict={
                    nc_image: nc_image_data,
                    art_image: art_image_data,
                    pv_image: pv_image_data
                })


            print('%d/%d: %s' % (iter + 1, len(image_names), image_name))
            to_txt(txt_path,
                   image_name, pv_image_data,
                   pixel_pos_scores, link_pos_scores)

    # create zip file for icdar2015
    cmd = 'cd %s;zip -j %s %s/*' % (dump_path, zip_path, txt_path)

    print(cmd)
    util.cmd.cmd(cmd)
    print("zip file created: ", util.io.join_path(dump_path, zip_path))


def test():
    with tf.name_scope('test'):
        image = tf.placeholder(dtype=tf.int32, shape = [None, None, 3])
        image_shape = tf.placeholder(dtype = tf.int32, shape = [3, ])
        processed_image, _, _, _, _ = ssd_vgg_preprocessing.preprocess_image(image, None, None, None, None, 
                                                   out_shape = config.image_shape,
                                                   data_format = config.data_format, 
                                                   is_training = False)
        b_image = tf.expand_dims(processed_image, axis = 0)
        net = pixel_link_symbol.PixelLinkNet(b_image, is_training = True)
        global_step = slim.get_or_create_global_step()

    
    sess_config = tf.ConfigProto(log_device_placement = False, allow_soft_placement = True)
    if FLAGS.gpu_memory_fraction < 0:
        sess_config.gpu_options.allow_growth = True
    elif FLAGS.gpu_memory_fraction > 0:
        sess_config.gpu_options.per_process_gpu_memory_fraction = FLAGS.gpu_memory_fraction
    
    checkpoint_dir = util.io.get_dir(FLAGS.checkpoint_path)
    logdir = util.io.join_path(checkpoint_dir, 'test', FLAGS.dataset_name + '_' +FLAGS.dataset_split_name)

    # Variables to restore: moving avg. or normal weights.
    if FLAGS.using_moving_average:
        variable_averages = tf.train.ExponentialMovingAverage(
                FLAGS.moving_average_decay)
        variables_to_restore = variable_averages.variables_to_restore()
        variables_to_restore[global_step.op.name] = global_step
    else:
        variables_to_restore = slim.get_variables_to_restore()
    
    saver = tf.train.Saver(var_list = variables_to_restore)
    
    
    image_names = util.io.ls(FLAGS.dataset_dir)
    image_names.sort()
    
    checkpoint = FLAGS.checkpoint_path
    checkpoint_name = util.io.get_filename(str(checkpoint));
    dump_path = util.io.join_path(logdir, checkpoint_name)
    txt_path = util.io.join_path(dump_path,'txt')        
    zip_path = util.io.join_path(dump_path, checkpoint_name + '_det.zip')
    
    with tf.Session(config = sess_config) as sess:
        saver.restore(sess, checkpoint)

        for iter, image_name in enumerate(image_names):
            image_data = util.img.imread(
                util.io.join_path(FLAGS.dataset_dir, image_name), rgb = True)
            image_name = image_name.split('.')[0]
            pixel_pos_scores, link_pos_scores = sess.run(
                [net.pixel_pos_scores, net.link_pos_scores], 
                feed_dict = {
                    image:image_data
            })
               
            print('%d/%d: %s'%(iter + 1, len(image_names), image_name))
            to_txt(txt_path,
                    image_name, image_data, 
                    pixel_pos_scores, link_pos_scores)

            
    # create zip file for icdar2015
    cmd = 'cd %s;zip -j %s %s/*'%(dump_path, zip_path, txt_path)

    print(cmd)
    util.cmd.cmd(cmd)
    print("zip file created: ", util.io.join_path(dump_path, zip_path))



def main(_):
    config_initialization()
    print('the multiphase_multislice_flag is ', FLAGS.multiphase_multislice_flag)

    # create the path
    if FLAGS.pred_path is not None and not os.path.exists(FLAGS.pred_path):
        os.mkdir(FLAGS.pred_path)
        print('mkdir: ', FLAGS.pred_path)
    if FLAGS.score_map_path is not None and not os.path.exists(FLAGS.score_map_path):
        os.mkdir(FLAGS.score_map_path)
        print('mkdir: ', FLAGS.score_map_path)
    if FLAGS.seg_map_path is not None and not os.path.exists(FLAGS.seg_map_path):
        os.mkdir(FLAGS.seg_map_path)
        print('mkdir: ', FLAGS.seg_map_path)

    if not FLAGS.multiphase_multislice_flag:
        print('test')
        test()
    else:
        if not FLAGS.clstm_flag:
            print('test_multiphase_multislice')
            test_multiphase_multislice()
        elif not FLAGS.mask_flag:
            print('test_multiphase_multislice_clstm')
            test_multiphase_multislice_clstm()
        else:
            print('test_multiphase_multislice_clstm_mask')
            test_multiphase_multislice_clstm_mask()
    
if __name__ == '__main__':
    tf.app.run()
