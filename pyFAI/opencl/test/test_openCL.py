#!/usr/bin/env python3
# coding: utf-8
#
#    Project: Azimuthal integration
#             https://github.com/silx-kit/pyFAI
#
#    Copyright (C) 2015-2020 European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Jérôme Kieffer (Jerome.Kieffer@ESRF.eu)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"test suite for OpenCL code"

__author__ = "Jérôme Kieffer"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "MIT"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "20/01/2021"

import unittest
import os
import time
import fabio
import numpy
import logging
import shutil
import platform

logger = logging.getLogger(__name__)

from .. import ocl
if ocl is not None:
    from .. import pyopencl, read_cl_file
    import pyopencl.array
from ... import load
from ...test  import utilstest
from ... import load_integrators
from ...method_registry import IntegrationMethod
from ...test.utilstest import test_options
from ...utils import mathutil
from ...utils.decorators import depreclog


class TestMask(unittest.TestCase):

    def setUp(self):
        if not test_options.opencl:
            self.skipTest("User request to skip OpenCL tests")
        if pyopencl is None or ocl is None:
            self.skipTest("OpenCL module (pyopencl) is not present or no device available")

        self.tmp_dir = os.path.join(test_options.tempdir, "opencl")
        if not os.path.isdir(self.tmp_dir):
            os.makedirs(self.tmp_dir)

        self.N = 500
        self.datasets = [{"img": test_options.getimage("Pilatus1M.edf"),
                          "poni": test_options.getimage("Pilatus1M.poni"),
                          "spline": None},
#                          {"img": test_options.getimage("halfccd.edf"),
#                           "poni": test_options.getimage("halfccd.poni"),
#                           "spline": test_options.getimage("halfccd.spline")},
#                          {"img": test_options.getimage("Frelon2k.edf"),
#                           "poni": test_options.getimage("Frelon2k.poni"),
#                           "spline": test_options.getimage("frelon.spline")},
#                          {"img": test_options.getimage("Pilatus6M.cbf"),
#                           "poni": test_options.getimage("Pilatus6M.poni"),
#                           "spline": None},
                         ]
        for ds in self.datasets:
            if ds["spline"] is not None:
                with open(ds["poni"], "r") as ponifile:
                    data = ponifile.read()
                # spline = os.path.basename(ds["spline"])
                with open(ds["poni"]) as f:
                    data = []
                    for line in f:
                        if line.startswith("SplineFile:"):
                            data.append("SplineFile: " + ds["spline"])
                        else:
                            data.append(line.strip())
                ds["poni"] = os.path.join(self.tmp_dir, os.path.basename(ds["poni"]))
                with open(ds["poni"], "w") as f:
                    f.write(os.linesep.join(data))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)
        self.tmp_dir = self.N = self.datasets = None

    @unittest.skipIf(test_options.low_mem, "test using >200M")
    def test_histogram(self):
        logger.info("Testing histogram-based algorithm (forward-integration)")
        ids = ocl.select_device("ALL", extensions=["cl_khr_int64_base_atomics"], memory=1e8)
        to_test = [v for k, v in IntegrationMethod._registry.items() if k.target == ids and k.split == "no" and k.algo == "histogram" and k.dim == 1]

        for ds in self.datasets:
            ai = load(ds["poni"])
            data = fabio.open(ds["img"]).data
            ref = ai.integrate1d_ng(data, self.N, method=("no", "histogram", "cython"), unit="2th_deg")
            for method in to_test:
                res = ai.integrate1d_ng(data, self.N, method=method, unit="2th_deg")
                r = mathutil.rwp(ref, res)
                logger.info(f"OpenCL {method} has R={r}  (vs cython) for dataset {ds}")
                self.assertLess(r, 3, "Rwp=%.3f for OpenCL histogram processing of %s" % (r, ds))

    @unittest.skipIf(test_options.low_mem, "test using >500M")
    def test_OpenCL_sparse(self):
        logger.info("Testing LUT-based algorithm (backward-integration)")
        ids = ocl.select_device("ALL", best=True, memory=1e8)
        to_test = [v for k, v in IntegrationMethod._registry.items() if k.target == ids and k.split == "bbox" and k.algo in ("lut", "csr") and k.dim == 1]
        for ds in self.datasets:
            ai = load(ds["poni"])
            data = fabio.open(ds["img"]).data
            ref = ai.integrate1d_ng(data, self.N, method=("bbox", "histogram", "cython"), unit="2th_deg")
            for method in to_test:
                res = ai.integrate1d_ng(data, self.N, method=method, unit="2th_deg")
                r = mathutil.rwp(ref, res)
                logger.info(f"OpenCL {method} has R={r}  (vs cython) for dataset {ds}")
                self.assertLess(r, 3, "Rwp=%.3f for OpenCL histogram processing of %s" % (r, ds))

    @unittest.skipIf(test_options.low_mem, "test using >200M")
    def test_OpenCL_sigma_clip(self):
        logger.info("Testing OpenCL sigma-clipping")
        ids = ocl.select_device("ALL", best=True, memory=1e8)
#         print(ids)
        to_test = [v for k, v in IntegrationMethod._registry.items() if k.target == ids and k.split == "no" and k.algo == "csr" and k.dim == 1]
        N = 100
#         print(to_test)
        for ds in self.datasets:
            ai = load(ds["poni"])
            data = fabio.open(ds["img"]).data
            ref = ai.integrate1d_ng(data, N, method=("no", "histogram", "cython"), unit="2th_deg")
            for method  in  to_test:
#                 print(method)
                try:
                    res = ai.sigma_clip_ng(data, N, method=method, unit="2th_deg")
                except (pyopencl.MemoryError, MemoryError, pyopencl.RuntimeError, RuntimeError) as error:
                    logger.warning("Memory error on %s dataset %s: %s%s. Converted into Warning: device may not have enough memory.", method, os.path.basename(ds["img"]), os.linesep, error)
                    break
                else:
                    # This is not really a precise test.
                    r = mathutil.rwp(ref, res)
                    logger.info("OpenCL sigma clipping has R= %.3f for dataset %s", r, ds)
#                     print(r)
                    self.assertLess(r, 10, "Rwp=%.3f for OpenCL CSR processing of %s" % (r, ds))


@unittest.skipIf(test_options.opencl is False, "User request to skip OpenCL tests")
@unittest.skipIf(ocl is None, "OpenCL is not available")
class TestSort(unittest.TestCase):
    """
    Test the kernels for vector and image sorting
    """

    @classmethod
    def setUpClass(cls):
        super(TestSort, cls).setUpClass()
        cls.N = 1024
        cls.ws = cls.N // 8

        cls.h_data = numpy.random.random(cls.N).astype("float32")
        cls.h2_data = numpy.random.random((cls.N, cls.N)).astype("float32").reshape((cls.N, cls.N))

        cls.ctx = ocl.create_context(devicetype="GPU")
        device = cls.ctx.devices[0]
        try:
            devtype = pyopencl.device_type.to_string(device.type).upper()
        except ValueError:
            # pocl does not describe itself as a CPU !
            devtype = "CPU"
        workgroup = device.max_work_group_size
        if (devtype == "CPU") and (device.platform.vendor == "Apple"):
            logger.info("For Apple's OpenCL on CPU: enforce max_work_goup_size=1")
            workgroup = 1

        cls.ws = min(workgroup, cls.ws)
        cls.queue = pyopencl.CommandQueue(cls.ctx, properties=pyopencl.command_queue_properties.PROFILING_ENABLE)
        cls.local_mem = pyopencl.LocalMemory(cls.ws * 32)  # 2float4 = 2*4*4 bytes per workgroup size
        src = read_cl_file("pyfai:openCL/bitonic.cl")
        cls.prg = pyopencl.Program(cls.ctx, src).build()

    @classmethod
    def tearDownClass(cls):
        super(TestSort, cls).tearDownClass()
        cls.h_data = None
        cls.queue = None
        cls.ctx = None
        cls.local_mem = None
        cls.h2_data = None

    @staticmethod
    def extra_skip(ctx):
        "This is a known buggy configuration"
        device = ctx.devices[0]
        if ("apple" in device.platform.name.lower() and
            "cpu" in pyopencl.device_type.to_string(device.type).lower()):
            logger.info("Apple CPU driver spotted, skipping")
            return True
        if ("portable" in device.platform.name.lower() and
            "cpu" in pyopencl.device_type.to_string(device.type).lower()):
            logger.info("PoCL CPU driver spotted, skipping")
            return True
        return False

    def test_reference_book(self):
        if self.extra_skip(self.ctx): return
        d_data = pyopencl.array.to_device(self.queue, self.h_data)
        t0 = time.perf_counter()
        hs_data = numpy.sort(self.h_data)
        t1 = time.perf_counter()
        time_sort = 1e3 * (t1 - t0)

        evt = self.prg.bsort_book(self.queue, (self.ws,), (self.ws,), d_data.data, self.local_mem)
        evt.wait()
        err = abs(hs_data - d_data.get()).max()
        logger.info("test_reference_book")
        logger.info("Numpy sort on %s element took %s ms", self.N, time_sort)
        logger.info("Reference sort time: %s ms, err=%s ", 1e-6 * (evt.profile.end - evt.profile.start), err)
        # this test works under linux:
        if platform.system() == "Linux":
            self.assertTrue(err == 0.0)
        else:
            logger.warning("Measured error on %s is %s", platform.system(), err)

    def test_reference_file(self):
        if self.extra_skip(self.ctx): return
        d_data = pyopencl.array.to_device(self.queue, self.h_data)
        t0 = time.perf_counter()
        hs_data = numpy.sort(self.h_data)
        t1 = time.perf_counter()
        time_sort = 1e3 * (t1 - t0)

        evt = self.prg.bsort_file(self.queue, (self.ws,), (self.ws,), d_data.data, self.local_mem)
        evt.wait()
        err = abs(hs_data - d_data.get()).max()
        logger.info("test_reference_file")
        logger.info("Numpy sort on %s element took %s ms", self.N, time_sort)
        logger.info("Reference sort time: %s ms, err=%s", 1e-6 * (evt.profile.end - evt.profile.start), err)
        # this test works anywhere !
        self.assertEqual(err, 0.0)

    def test_sort_all(self):
        if self.extra_skip(self.ctx): return
        d_data = pyopencl.array.to_device(self.queue, self.h_data)
        t0 = time.perf_counter()
        hs_data = numpy.sort(self.h_data)
        t1 = time.perf_counter()
        time_sort = 1e3 * (t1 - t0)

        evt = self.prg.bsort_all(self.queue, (self.ws,), (self.ws,), d_data.data, self.local_mem)
        evt.wait()
        err = abs(hs_data - d_data.get()).max()
        logger.info("test_sort_all")
        logger.info("Numpy sort on %s element took %s ms", self.N, time_sort)
        logger.info("modified function execution time: %s ms, err=%s", 1e-6 * (evt.profile.end - evt.profile.start), err)
        self.assertEqual(err, 0.0)

    def test_sort_horizontal(self):
        if self.extra_skip(self.ctx): return
        d2_data = pyopencl.array.to_device(self.queue, self.h2_data)
        t0 = time.perf_counter()
        h2s_data = numpy.sort(self.h2_data, axis=-1)
        t1 = time.perf_counter()
        time_sort = 1e3 * (t1 - t0)
        evt = self.prg.bsort_horizontal(self.queue, (self.N, self.ws), (1, self.ws), d2_data.data, self.local_mem)
        evt.wait()
        err = abs(h2s_data - d2_data.get()).max()
        logger.info("Numpy horizontal sort on %sx%s elements took %s ms", self.N, self.N, time_sort)
        logger.info("Horizontal execution time: %s ms, err=%s", 1e-6 * (evt.profile.end - evt.profile.start), err)
        self.assertEqual(err, 0.0)

    def test_sort_vertical(self):
        if self.extra_skip(self.ctx): return
        d2_data = pyopencl.array.to_device(self.queue, self.h2_data)
        t0 = time.perf_counter()
        h2s_data = numpy.sort(self.h2_data, axis=0)
        t1 = time.perf_counter()
        time_sort = 1e3 * (t1 - t0)
        evt = self.prg.bsort_vertical(self.queue, (self.ws, self.N), (self.ws, 1), d2_data.data, self.local_mem)
        evt.wait()
        err = abs(h2s_data - d2_data.get()).max()
        logger.info("Numpy vertical sort on %sx%s elements took %s ms", self.N, self.N, time_sort)
        logger.info("Vertical execution time: %s ms, err=%s ", 1e-6 * (evt.profile.end - evt.profile.start), err)
        self.assertEqual(err, 0.0)


class TestKahan(unittest.TestCase):
    """
    Test the kernels for compensated math in OpenCL
    """

    def setUp(self):
        if not test_options.opencl:
            self.skipTest("User request to skip OpenCL tests")
        if pyopencl is None or ocl is None:
            self.skipTest("OpenCL module (pyopencl) is not present or no device available")

        self.ctx = ocl.create_context(devicetype="GPU")
        self.queue = pyopencl.CommandQueue(self.ctx, properties=pyopencl.command_queue_properties.PROFILING_ENABLE)

        # this is running 32 bits OpenCL with POCL
        if (platform.machine() in ("i386", "i686", "x86_64") and (tuple.__itemsize__ == 4) and
                self.ctx.devices[0].platform.name == 'Portable Computing Language'):
            self.args = "-DX87_VOLATILE=volatile"
        else:
            self.args = ""

    def tearDown(self):
        self.queue = None
        self.ctx = None

    @staticmethod
    def dummy_sum(ary, dtype=None):
        "perform the actual sum in a dummy way "
        if dtype is None:
            dtype = ary.dtype.type
        sum_ = dtype(0)
        for i in ary:
            sum_ += i
        return sum_

    def test_kahan(self):
        # simple test
        N = 26
        data = (1 << (N - 1 - numpy.arange(N))).astype(numpy.float32)

        ref64 = numpy.sum(data, dtype=numpy.float64)
        ref32 = self.dummy_sum(data)
        if (ref64 == ref32):
            logger.warning("Kahan: invalid tests as float32 provides the same result as float64")
        # Dummy kernel to evaluate
        src = """
        kernel void summation(global float* data,
                                           int size,
                                    global float* result)
        {
            float2 acc = (float2)(0.0f, 0.0f);
            for (int i=0; i<size; i++)
            {
                acc = kahan_sum(acc, data[i]);
            }
            result[0] = acc.s0;
            result[1] = acc.s1;
        }
        """
        prg = pyopencl.Program(self.ctx, read_cl_file("pyfai:openCL/kahan.cl") + src).build(self.args)
        ones_d = pyopencl.array.to_device(self.queue, data)
        res_d = pyopencl.array.zeros(self.queue, 2, numpy.float32)
        evt = prg.summation(self.queue, (1,), (1,), ones_d.data, numpy.int32(N), res_d.data)
        evt.wait()
        res = res_d.get().sum(dtype=numpy.float64)
        self.assertEqual(ref64, res, "test_kahan")

    def test_dot16(self):
        # simple test
        N = 16
        data = (1 << (N - 1 - numpy.arange(N))).astype(numpy.float32)

        ref64 = numpy.dot(data.astype(numpy.float64), data.astype(numpy.float64))
        ref32 = numpy.dot(data, data)
        if (ref64 == ref32):
            logger.warning("dot16: invalid tests as float32 provides the same result as float64")
        # Dummy kernel to evaluate
        src = """
        kernel void test_dot16(global float* data,
                                           int size,
                               global float* result)
        {
            float2 acc = (float2)(0.0f, 0.0f);
            float16 data16 = (float16) (data[0],data[1],data[2],data[3],data[4],
                                        data[5],data[6],data[7],data[8],data[9],
                         data[10],data[11],data[12],data[13],data[14],data[15]);
            acc = comp_dot16(data16, data16);
            result[0] = acc.s0;
            result[1] = acc.s1;
        }

        kernel void test_dot8(global float* data,
                                           int size,
                               global float* result)
        {
            float2 acc = (float2)(0.0f, 0.0f);
            float8 data0 = (float8) (data[0],data[2],data[4],data[6],data[8],data[10],data[12],data[14]);
            float8 data1 = (float8) (data[1],data[3],data[5],data[7],data[9],data[11],data[13],data[15]);
            acc = comp_dot8(data0, data1);
            result[0] = acc.s0;
            result[1] = acc.s1;
        }

        kernel void test_dot4(global float* data,
                                           int size,
                               global float* result)
        {
            float2 acc = (float2)(0.0f, 0.0f);
            float4 data0 = (float4) (data[0],data[4],data[8],data[12]);
            float4 data1 = (float4) (data[3],data[7],data[11],data[15]);
            acc = comp_dot4(data0, data1);
            result[0] = acc.s0;
            result[1] = acc.s1;
        }

        kernel void test_dot3(global float* data,
                                           int size,
                               global float* result)
        {
            float2 acc = (float2)(0.0f, 0.0f);
            float3 data0 = (float3) (data[0],data[4],data[12]);
            float3 data1 = (float3) (data[3],data[11],data[15]);
            acc = comp_dot3(data0, data1);
            result[0] = acc.s0;
            result[1] = acc.s1;
        }

        kernel void test_dot2(global float* data,
                                           int size,
                               global float* result)
        {
            float2 acc = (float2)(0.0f, 0.0f);
            float2 data0 = (float2) (data[0],data[14]);
            float2 data1 = (float2) (data[1],data[15]);
            acc = comp_dot2(data0, data1);
            result[0] = acc.s0;
            result[1] = acc.s1;
        }

        """

        prg = pyopencl.Program(self.ctx, read_cl_file("pyfai:openCL/kahan.cl") + src).build(self.args)
        ones_d = pyopencl.array.to_device(self.queue, data)
        res_d = pyopencl.array.zeros(self.queue, 2, numpy.float32)
        evt = prg.test_dot16(self.queue, (1,), (1,), ones_d.data, numpy.int32(N), res_d.data)
        evt.wait()
        res = res_d.get().sum(dtype="float64")
        self.assertEqual(ref64, res, "test_dot16")

        res_d.fill(0)
        data0 = data[0::2]
        data1 = data[1::2]
        ref64 = numpy.dot(data0.astype(numpy.float64), data1.astype(numpy.float64))
        ref32 = numpy.dot(data0, data1)
        if (ref64 == ref32):
            logger.warning("dot8: invalid tests as float32 provides the same result as float64")
        evt = prg.test_dot8(self.queue, (1,), (1,), ones_d.data, numpy.int32(N), res_d.data)
        evt.wait()
        res = res_d.get().sum(dtype="float64")
        self.assertEqual(ref64, res, "test_dot8")

        res_d.fill(0)
        data0 = data[0::4]
        data1 = data[3::4]
        ref64 = numpy.dot(data0.astype(numpy.float64), data1.astype(numpy.float64))
        ref32 = numpy.dot(data0, data1)
        if (ref64 == ref32):
            logger.warning("dot4: invalid tests as float32 provides the same result as float64")
        evt = prg.test_dot4(self.queue, (1,), (1,), ones_d.data, numpy.int32(N), res_d.data)
        evt.wait()
        res = res_d.get().sum(dtype="float64")
        self.assertEqual(ref64, res, "test_dot4")

        res_d.fill(0)
        data0 = numpy.array([data[0], data[4], data[12]])
        data1 = numpy.array([data[3], data[11], data[15]])
        ref64 = numpy.dot(data0.astype(numpy.float64), data1.astype(numpy.float64))
        ref32 = numpy.dot(data0, data1)
        if (ref64 == ref32):
            logger.warning("dot3: invalid tests as float32 provides the same result as float64")
        evt = prg.test_dot3(self.queue, (1,), (1,), ones_d.data, numpy.int32(N), res_d.data)
        evt.wait()
        res = res_d.get().sum(dtype="float64")
        self.assertEqual(ref64, res, "test_dot3")

        res_d.fill(0)
        data0 = numpy.array([data[0], data[14]])
        data1 = numpy.array([data[1], data[15]])
        ref64 = numpy.dot(data0.astype(numpy.float64), data1.astype(numpy.float64))
        ref32 = numpy.dot(data0, data1)
        if (ref64 == ref32):
            logger.warning("dot2: invalid tests as float32 provides the same result as float64")
        evt = prg.test_dot2(self.queue, (1,), (1,), ones_d.data, numpy.int32(N), res_d.data)
        evt.wait()
        res = res_d.get().sum(dtype="float64")
        self.assertEqual(ref64, res, "test_dot2")


def suite():
    testsuite = unittest.TestSuite()
    loader = unittest.defaultTestLoader.loadTestsFromTestCase
    testsuite.addTest(loader(TestMask))
    testsuite.addTest(loader(TestSort))
    testsuite.addTest(loader(TestKahan))
    return testsuite


if __name__ == '__main__':
    runner = unittest.TextTestRunner()
    runner.run(suite())
