import copy
import sys
import warnings

import numpy as np

from skrobot.coordinates.dual_quaternion import DualQuaternion
from skrobot.coordinates.math import _check_valid_rotation
from skrobot.coordinates.math import _check_valid_translation
from skrobot.coordinates.math import angle_between_vectors
from skrobot.coordinates.math import convert_to_axis_vector
from skrobot.coordinates.math import cross_product
from skrobot.coordinates.math import matrix2quaternion
from skrobot.coordinates.math import matrix2ypr
from skrobot.coordinates.math import matrix_log
from skrobot.coordinates.math import normalize_vector
from skrobot.coordinates.math import quaternion2matrix
from skrobot.coordinates.math import quaternion_multiply
from skrobot.coordinates.math import random_rotation
from skrobot.coordinates.math import random_translation
from skrobot.coordinates.math import rotate_matrix
from skrobot.coordinates.math import rotation_angle
from skrobot.coordinates.math import rotation_matrix
from skrobot.coordinates.math import rpy2quaternion
from skrobot.coordinates.math import rpy_angle
from skrobot.coordinates.math import wxyz2xyzw
from skrobot.coordinates.math import xyzw2wxyz


def transform_coords(c1, c2, out=None):
    """Return Coordinates by applying c1 to c2 from the left

    Parameters
    ----------
    c1 : skrobot.coordinates.Coordinates
    c2 : skrobot.coordinates.Coordinates
        Coordinates
    c3 : skrobot.coordinates.Coordinates or None
        Output argument. If this value is specified, the results will be
        in-placed.

    Returns
    -------
    Coordinates(pos=translation, rot=q) : skrobot.coordinates.Coordinates
        new coordinates

    Examples
    --------
    >>> from skrobot.coordinates import Coordinates
    >>> from skrobot.coordinates import transform_coords
    >>> from numpy import pi
    >>> c1 = Coordinates()
    >>> c2 = Coordinates()
    >>> c3 = transform_coords(c1, c2)
    >>> c3.translation
    array([0., 0., 0.])
    >>> c3.rotation
    array([[1., 0., 0.],
           [0., 1., 0.],
           [0., 0., 1.]])
    >>> c1 = Coordinates().translate([0.1, 0.2, 0.3]).rotate(pi / 3.0, 'x')
    >>> c2 = Coordinates().translate([0.3, -0.3, 0.1]).rotate(pi / 2.0, 'y')
    >>> c3 = transform_coords(c1, c2)
    >>> c3.translation
    array([ 0.4       , -0.03660254,  0.09019238])
    >>> c3.rotation
    >>> c3.rotation
    array([[ 1.94289029e-16,  0.00000000e+00,  1.00000000e+00],
           [ 8.66025404e-01,  5.00000000e-01, -1.66533454e-16],
           [-5.00000000e-01,  8.66025404e-01,  2.77555756e-17]])
    """
    if out is None:
        out = Coordinates(check_validity=False)
    elif not isinstance(out, Coordinates):
        raise TypeError("Input type should be skrobot.coordinates.Coordinates")
    out._translation = c1._translation + np.dot(c1._rotation, c2._translation)
    out._rotation = np.matmul(c1._rotation, c2._rotation)
    return out


class Transform(object):
    """Transform specified by translation and rotation

    Parameters
    ----------
    translation : list(3,) or numpy.ndarray(3,)
        translation
    rot : numpy.ndarray(3, 3)
        3x3 rotation matrix
    """

    def __init__(self, translation, rotation):
        self.translation = np.array(translation)
        self.rotation = rotation

    def transform_vector(self, vec):
        """Apply this transform to vector/vectors

        Parameters
        ----------
        vec : numpy.ndarray(3,) or numpy.ndarray(n_points, 3)
            vector/vectors to be transformed

        Returns
        -------
        vec_transformed : numpy.ndarray(3,) or numpy.ndarray(n_points, 3)
            transformed points
        """
        assert vec.ndim < 3, "vec must be either 1 or 2 dimensional."
        if vec.ndim == 1:
            return self.rotation.dot(vec.T) + self.translation
        if vec.ndim == 2:
            return self.rotation.dot(vec.T).T + self.translation[None, :]

    def rotate_vector(self, vec):
        """Rotate 3-dimensional vector using rotation of this Transform

        Parameters
        ----------
        vec : numpy.ndarray(3,) or numpy.ndarray(3, n_points)
            vector (or vectors) to be rotated

        Returns
        -------
        vec_transformed : numpy.ndarray(3,), numpy.ndarray(3, n_points)
            rotated vector (or vectors)
        """
        assert vec.ndim < 3, "vec must be either 1 or 2 dimensional."
        if vec.ndim == 1:
            return self.rotation.dot(vec.T)
        if vec.ndim == 2:
            return self.rotation.dot(vec.T).T

    def inverse_transformation(self):
        """Return inverse transform

        Returns
        -------
        inv_transform : skrobot.coordinates.base.Transform
            inverse transformation
        """
        new_rot = self.rotation.T
        new_trans = -new_rot.dot(self.translation)
        return Transform(new_trans, new_rot)

    def __mul__(self, tf_23):
        """Composite this transform with other transform

        Parameters
        ----------
        tf_23 : skrobot.coordinates.base.Transform
            the other transform.

        Returns
        -------
        tf_13 : skrobot.coordinates.base.Transform
            Let this (self) transform as tf_12, then with the
            other transform tf_23, we obtain tf_13 = tf_12 * tf_23
        """
        tf_12 = self
        tran_12, rot_12 = tf_12.translation, tf_12.rotation
        tran_23, rot_23 = tf_23.translation, tf_23.rotation
        rot_13 = rot_23.dot(rot_12)
        tran_13 = tran_23 + rot_23.dot(tran_12)
        tf_13 = Transform(tran_13, rot_13)
        return tf_13

    def __rmul__(self, other):
        return other.__mul__(self)


class Coordinates(object):

    """Coordinates class to manipulate rotation and translation.

    Parameters
    ----------
    pos : list or numpy.ndarray or None
        shape of (3,) translation vector. or
        4x4 homogeneous transformation matrix.
        If the homogeneous transformation matrix is given,
        `rot` will be overwritten.
        If this value is `None`, set [0, 0, 0] vector as default.
    rot : list or numpy.ndarray or None
        we can take 3x3 rotation matrix or
        [yaw, pitch, roll] or
        quaternion [w, x, y, z] order
        If this value is `None`, set the identity matrix as default.
    name : str or None
        name of this coordinates
    check_validity : bool (optional)
        Default `True`.
        If this value is `True`, check whether an input rotation
        and an input translation are valid.
    """

    def __init__(self,
                 pos=None,
                 rot=None,
                 name=None,
                 hook=None,
                 check_validity=True,
                 input_quaternion_order='wxyz'):
        if check_validity:
            if (isinstance(pos, list) or isinstance(pos, np.ndarray)):
                T = np.array(pos, dtype=np.float64)
                if T.shape == (4, 4):
                    pos = T[:3, 3]
                    rot = T[:3, :3]
            if rot is None:
                self._rotation = np.eye(3)
            else:
                if input_quaternion_order == 'wxyz':
                    pass
                elif input_quaternion_order == 'xyzw':
                    if np.array(rot).shape == (4,):
                        rot = xyzw2wxyz(rot)
                else:
                    msg = "Invalid input_quaternion_order: "
                    msg += "{}. Must be 'wxyz' or 'xyzw'.".format(
                        input_quaternion_order)
                    raise ValueError(msg)
                self.rotation = rot
            if pos is None:
                self._translation = np.array([0, 0, 0])
            else:
                self.translation = pos
        else:
            if rot is None:
                self._rotation = np.eye(3)
            else:
                self._rotation = rot
            if pos is None:
                self._translation = np.array([0, 0, 0])
            else:
                self._translation = pos
        if name is None:
            name = ''
        self.name = name
        self.parent = None
        self._hook = hook

    def disable_hook(self):
        if self._hook is not None:
            original_hook = self._hook
            self._hook = None
            return True, original_hook
        return False, None

    def get_transform(self):
        """Return Transform object

        Returns
        -------
        transform : skrobot.coordinates.base.Transform
            corresponding Transform to this coordinates
        """
        return Transform(self.worldpos(), self.worldrot())

    @property
    def rotation(self):
        """Return rotation matrix of this coordinates.

        Returns
        -------
        self._rotation : numpy.ndarray
            3x3 rotation matrix

        Examples
        --------
        >>> import numpy as np
        >>> from skrobot.coordinates import Coordinates
        >>> c = Coordinates()
        >>> c.rotation
        array([[1., 0., 0.],
               [0., 1., 0.],
               [0., 0., 1.]])
        >>> c.rotate(np.pi / 2.0, 'y')
        >>> c.rotation
        array([[ 2.22044605e-16,  0.00000000e+00,  1.00000000e+00],
               [ 0.00000000e+00,  1.00000000e+00,  0.00000000e+00],
               [-1.00000000e+00,  0.00000000e+00,  2.22044605e-16]])
        """
        if self._hook is not None:
            self._hook()
        return self._rotation

    @rotation.setter
    def rotation(self, rotation):
        """Set rotation of this coordinate

        This setter checks the given rotation and set it this coordinate.

        Parameters
        ----------
        rotation : list or numpy.ndarray
            we can take 3x3 rotation matrix or
            rpy angle [yaw, pitch, roll] or
            quaternion [w, x, y, z] order
        """
        rotation = np.array(rotation)
        # Convert quaternions
        if rotation.shape == (4,):
            q = np.array([q for q in rotation])
            if np.abs(np.linalg.norm(q) - 1.0) > 1e-3:
                raise ValueError('Invalid quaternion. Must be '
                                 'norm 1.0, get {}'.
                                 format(np.linalg.norm(q)))
            rotation = quaternion2matrix(q)
        elif rotation.shape == (3,):
            # Convert [yaw-pitch-roll] to rotation matrix
            q = rpy2quaternion(rotation)
            rotation = quaternion2matrix(q)

        # Convert lists and tuples
        if type(rotation) in (list, tuple):
            rotation = np.array(rotation).astype(np.float32)

        _check_valid_rotation(rotation)
        self._rotation = rotation * 1.

    @property
    def translation(self):
        """Return translation of this coordinates.

        Returns
        -------
        self._translation : numpy.ndarray
            vector shape of (3, ). unit is [m]

        Examples
        --------
        >>> from skrobot.coordinates import Coordinates
        >>> c = Coordinates()
        >>> c.translation
        array([0., 0., 0.])
        >>> c.translate([0.1, 0.2, 0.3])
        >>> c.translation
        array([0.1, 0.2, 0.3])
        """
        if self._hook is not None:
            self._hook()
        return self._translation

    @translation.setter
    def translation(self, translation):
        """Set translation of this coordinate

        This setter checks the given translation and set it this coordinate.

        Parameters
        ----------
        translation : list or tuple or numpy.ndarray
            shape of (3,) translation vector
        """
        # Convert lists to translation arrays
        if type(translation) in (list, tuple) and len(translation) == 3:
            translation = np.array([t for t in translation]).astype(np.float64)

        _check_valid_translation(translation)
        self._translation = translation.squeeze() * 1.

    @property
    def name(self):
        """Return this coordinate's name

        Returns
        -------
        self._name : str
            name of this coordinate
        """
        return self._name

    @name.setter
    def name(self, name):
        """Setter of this coordinate's name

        Parameters
        ----------
        name : str
            name of this coordinate
        """
        if not isinstance(name, str):
            raise TypeError('name should be string, get {}'.
                            format(type(name)))
        self._name = name

    @property
    def dimension(self):
        """Return dimension of this coordinate

        Returns
        -------
        len(self.translation) : int
            dimension of this coordinate
        """
        return len(self._translation)

    @property
    def x_axis(self):
        """Return x axis vector of this coordinates.

        Returns
        -------
        axis : numpy.ndarray
            x axis.
        """
        return np.array(self._rotation[:, 0].T, 'f')

    @property
    def y_axis(self):
        """Return y axis vector of this coordinates.

        Returns
        -------
        axis : numpy.ndarray
            y axis.
        """
        return np.array(self._rotation[:, 1].T, 'f')

    @property
    def z_axis(self):
        """Return z axis vector of this coordinates.

        Returns
        -------
        axis : numpy.ndarray
            z axis.
        """
        return np.array(self._rotation[:, 2].T, 'f')

    def changed(self):
        """Return False

        This is used for CascadedCoords compatibility

        Returns
        -------
        False : bool
            always return False
        """
        return False

    def translate(self, vec, wrt='local'):
        """Translate this coordinates.

        Note that this function changes this coordinates self.
        So if you don't want to change this class, use copy_worldcoords()

        Parameters
        ----------
        vec : list or numpy.ndarray
            shape of (3,) translation vector. unit is [m] order.
        wrt : str or Coordinates (optional)
            translate with respect to wrt.

        Examples
        --------
        >>> import numpy as np
        >>> from skrobot.coordinates import Coordinates
        >>> c = Coordinates()
        >>> c.translation
        array([0., 0., 0.], dtype=float32)
        >>> c.translate([0.1, 0.2, 0.3])
        >>> c.translation
        array([0.1, 0.2, 0.3], dtype=float32)

        >>> c = Coordinates()
        >>> c.copy_worldcoords().translate([0.1, 0.2, 0.3])
        >>> c.translation
        array([0., 0., 0.], dtype=float32)

        >>> c = Coordinates().rotate(np.pi / 2.0, 'y')
        >>> c.translate([0.1, 0.2, 0.3])
        >>> c.translation
        array([ 0.3,  0.2, -0.1])
        >>> c = Coordinates().rotate(np.pi / 2.0, 'y')
        >>> c.translate([0.1, 0.2, 0.3], 'world')
        >>> c.translation
        array([0.1, 0.2, 0.3])
        """
        vec = np.array(vec, dtype=np.float64)
        return self.newcoords(
            self._rotation,
            self.parent_orientation(vec, wrt) + self._translation,
            check_validity=False, relative_coords='local')

    def transform_vector(self, v):
        """"Return vector represented at world frame.

        Vector v given in the local coords is converted to world
        representation.

        Parameters
        ----------
        v : numpy.ndarray
            3d vector.
            We can take batch of vector like (batch_size, 3)
        Returns
        -------
        transformed_point : numpy.ndarray
            transformed point
        """
        v = np.array(v, dtype=np.float64)
        if v.ndim == 2:
            return (np.matmul(self._rotation, v.T)
                    + self._translation.reshape(3, -1)).T
        return np.matmul(self._rotation, v) + self._translation

    def inverse_transform_vector(self, vec):
        """Transform vector in world coordinates to local coordinates

        Parameters
        ----------
        vec : numpy.ndarray
            3d vector.
            We can take batch of vector like (batch_size, 3)
        Returns
        -------
        transformed_point : numpy.ndarray
            transformed point
        """
        vec = np.array(vec, dtype=np.float64)
        if vec.ndim == 2:
            return (np.matmul(self._rotation.T, vec.T)
                    - np.matmul(
                        self._rotation.T, self._translation).reshape(3, -1)).T
        return np.matmul(self._rotation.T, vec) - \
            np.matmul(self._rotation.T, self._translation)

    def inverse_transformation(self, dest=None):
        """Return a invese transformation of this coordinate system.

        Create a new coordinate with inverse transformation of this
        coordinate system.

        .. math::
            \\left(
                \\begin{array}{ccc}
                  R^{-1} & - R^{-1} p  \\\\
                  0 & 1
                \\end{array}
            \\right)

        Parameters
        ----------
        dest : None or skrobot.coordinates.Coordinates
            If dest is given, the result of transformation
            is in-placed to dest.

        Returns
        -------
        dest : skrobot.coordinates.Coordinates
            result of inverse transformation.
        """
        if dest is None:
            dest = Coordinates(check_validity=False)
        dest._rotation = self._rotation.T
        dest._translation = np.matmul(dest._rotation, self._translation)
        dest._translation = -1.0 * dest._translation
        return dest

    def transformation(self, c2, wrt='local'):
        c2 = c2.worldcoords()
        c1 = self.worldcoords()
        inv = c1.inverse_transformation()
        if wrt == 'local' or wrt == self:
            transform_coords(inv, c2, inv)
        elif wrt == 'parent' or \
                wrt == self.parent or \
                wrt == 'world':
            transform_coords(c2, inv, inv)
        elif isinstance(wrt, Coordinates):
            xw = wrt.worldcoords()
            transform_coords(c2, inv, inv)
            transform_coords(xw.inverse_transformation(), inv, inv)
            transform_coords(inv, xw, inv)
        else:
            raise ValueError('wrt {} not supported'.format(wrt))
        return inv

    def T(self):
        """Return 4x4 homogeneous transformation matrix.

        Returns
        -------
        matrix : numpy.ndarray
            homogeneous transformation matrix shape of (4, 4)

        Examples
        --------
        >>> from numpy import pi
        >>> from skrobot.coordinates import make_coords
        >>> c = make_coords()
        >>> c.T()
        array([[1., 0., 0., 0.],
               [0., 1., 0., 0.],
               [0., 0., 1., 0.],
               [0., 0., 0., 1.]])
        >>> c.translate([0.1, 0.2, 0.3])
        >>> c.rotate(pi / 2.0, 'y')
        array([[ 2.22044605e-16,  0.00000000e+00,  1.00000000e+00,
                 1.00000000e-01],
               [ 0.00000000e+00,  1.00000000e+00,  0.00000000e+00,
                 2.00000000e-01],
               [-1.00000000e+00,  0.00000000e+00,  2.22044605e-16,
                 3.00000000e-01],
               [ 0.00000000e+00,  0.00000000e+00,  0.00000000e+00,
                 1.00000000e+00]])
        """
        matrix = np.zeros((4, 4), dtype=np.float64)
        matrix[3, 3] = 1.0
        matrix[:3, :3] = self._rotation
        matrix[:3, 3] = self._translation
        return matrix

    @property
    def quaternion(self):
        """Property of quaternion in [w, x, y, z] format

        Returns
        -------
        q : numpy.ndarray
            [w, x, y, z] quaternion

        Examples
        --------
        >>> from numpy import pi
        >>> from skrobot.coordinates import make_coords
        >>> c = make_coords()
        >>> c.quaternion
        array([1., 0., 0., 0.])
        >>> c.rotate(pi / 3, 'y').rotate(pi / 5, 'z')
        >>> c.quaternion
        array([0.8236391 , 0.1545085 , 0.47552826, 0.26761657])
        """
        return matrix2quaternion(self._rotation)

    @property
    def quaternion_wxyz(self):
        """Property of quaternion in [w, x, y, z] format

        Returns
        -------
        q : numpy.ndarray
            [w, x, y, z] quaternion
        """
        return matrix2quaternion(self._rotation)

    @property
    def quaternion_xyzw(self):
        """Property of quaternion in [x, y, z, w] format

        Returns
        -------
        q : numpy.ndarray
            [x, y, z, w] quaternion

        Examples
        --------
        >>> from numpy import pi
        >>> from skrobot.coordinates import make_coords
        >>> c = make_coords()
        >>> c.quaternion_xyzw
        array([0., 0., 0., 1.])
        >>> c.rotate(pi / 3, 'y').rotate(pi / 5, 'z')
        >>> c.quaternion_xyzw
        array([0.1545085 , 0.47552826, 0.26761657, 0.8236391 ])
        """
        return wxyz2xyzw(matrix2quaternion(self._rotation))

    @property
    def dual_quaternion(self):
        """Property of DualQuaternion

        Return DualQuaternion representation of this coordinate.

        Returns
        -------
        DualQuaternion : skrobot.coordinates.dual_quaternion.DualQuaternion
            DualQuaternion representation of this coordinate
        """
        qr = normalize_vector(self.quaternion)
        x, y, z = self._translation
        qd = quaternion_multiply(np.array([0, x, y, z]), qr) * 0.5
        return DualQuaternion(qr, qd)

    def parent_orientation(self, v, wrt):
        if wrt == 'local' or wrt == self:
            return np.matmul(self._rotation, v)
        if wrt == 'parent' \
           or wrt == self.parent \
           or wrt == 'world':
            return v
        if coordinates_p(wrt):
            return np.matmul(wrt.worldrot(), v)
        raise ValueError('wrt {} not supported'.format(wrt))

    def rotate_vector(self, v):
        """Rotate 3-dimensional vector using rotation of this coordinate

        Parameters
        ----------
        v : numpy.ndarray
            vector shape of (3,)

        Returns
        -------
        np.matmul(self._rotation, v) : numpy.ndarray
            rotated vector

        Examples
        --------
        >>> from skrobot.coordinates import Coordinates
        >>> from numpy import pi
        >>> c = Coordinates().rotate(pi, 'z')
        >>> c.rotate_vector([1, 2, 3])
        array([-1., -2.,  3.])
        """
        return np.matmul(self._rotation, v)

    def inverse_rotate_vector(self, v):
        return np.matmul(v, self._rotation)

    def transform(self, c, wrt='local', out=None):
        """Transform this coordinates by coords based on wrt

        Note that this function changes this coordinates
        translation and rotation.
        If you would like not to change this coordinates,
        Please use copy_worldcoords() or give `out`.

        Parameters
        ----------
        c : skrobot.coordinates.Coordinates
            coordinate
        wrt : str or skrobot.coordinates.Coordinates
            If wrt is 'local' or self, multiply c from the right.
            If wrt is 'world' or 'parent' or self.parent,
            transform c with respect to worldcoord.
            If wrt is Coordinates, transform c with respect to c.
        out : None or skrobot.coordinates.Coordinates
            If the `out` is specified, set new coordinates to `out`.
            Note that if the `out` is given, these coordinates don't change.

        Returns
        -------
        self : skrobot.coordinates.Coordinates
            return this coordinate

        Examples
        --------
        """
        if out is None:
            out = self
        if wrt == 'local' or wrt == self:
            # multiply c from the right
            transform_coords(self, c, out)
        elif wrt == 'parent' or wrt == self.parent \
                or wrt == 'world':
            # multiply c from the left
            transform_coords(c, self, out)
        elif isinstance(wrt, Coordinates):
            transform_coords(wrt.inverse_transformation(), self, out)
            transform_coords(c, out, out)
            transform_coords(wrt.worldcoords(), out, out)
        else:
            raise ValueError('transform wrt {} is not supported'.format(wrt))
        return out

    def move_coords(self, target_coords, local_coords):
        """Transform this coordinate so that local_coords to target_coords.

        Parameters
        ----------
        target_coords : skrobot.coordinates.Coordinates
            target coords.
        local_coords : skrobot.coordinates.Coordinates
            local coords to be aligned.

        Returns
        -------
        self.worldcoords() : skrobot.coordinates.Coordinates
            world coordinates.
        """
        self.transform(
            local_coords.transformation(target_coords), local_coords)
        return self.worldcoords()

    def rpy_angle(self):
        """Return a pair of rpy angles of this coordinates.

        .. deprecated::
            This method is deprecated and confusing. Use matrix2ypr() or matrix2rpy() instead.

        Returns
        -------
        rpy_angle(self._rotation) : tuple(numpy.ndarray, numpy.ndarray)
            a pair of rpy angles. See also skrobot.coordinates.math.rpy_angle

        Examples
        --------
        >>> import numpy as np
        >>> from skrobot.coordinates import Coordinates
        >>> c = Coordinates().rotate(np.pi / 2.0, 'x').rotate(np.pi / 3.0, 'z')
        >>> r.rpy_angle()
        (array([ 3.84592537e-16, -1.04719755e+00,  1.57079633e+00]),
        array([ 3.14159265, -2.0943951 , -1.57079633]))
        """
        warnings.warn(
            "rpy_angle() method is deprecated and confusing. Use matrix2ypr() for [yaw, pitch, roll] "
            "or matrix2rpy() for [roll, pitch, yaw] instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return rpy_angle(self._rotation)

    def axis(self, ax):
        ax = convert_to_axis_vector(ax)
        return self.rotate_vector(ax)

    def difference_position(self, coords,
                            translation_axis=True):
        """Return differences in position of given coords.

        Parameters
        ----------
        coords : skrobot.coordinates.Coordinates
            given coordinates
        translation_axis : str or bool or None (optional)
            we can take 'x', 'y', 'z', 'xy', 'yz', 'zx', 'xx', 'yy', 'zz',
            True or False(None).

        Returns
        -------
        dif_pos : numpy.ndarray
            difference position of self coordinates and coords
            considering translation_axis.

        Examples
        --------
        >>> from skrobot.coordinates import Coordinates
        >>> from skrobot.coordinates import transform_coords
        >>> from numpy import pi
        >>> c1 = Coordinates().translate([0.1, 0.2, 0.3]).rotate(
        ...          pi / 3.0, 'x')
        >>> c2 = Coordinates().translate([0.3, -0.3, 0.1]).rotate(
        ...          pi / 2.0, 'y')
        >>> c1.difference_position(c2)
        array([ 0.2       , -0.42320508,  0.3330127 ])
        >>> c1 = Coordinates().translate([0.1, 0.2, 0.3]).rotate(0, 'x')
        >>> c2 = Coordinates().translate([0.3, -0.3, 0.1]).rotate(
        ...          pi / 3.0, 'x')
        >>> c1.difference_position(c2)
        array([ 0.2, -0.5, -0.2])
        """
        dif_pos = self.inverse_transform_vector(coords.worldpos())
        translation_axis = convert_to_axis_vector(translation_axis)
        dif_pos[translation_axis == 1] = 0.0
        return dif_pos

    def difference_rotation(self, coords,
                            rotation_axis=True):
        """Return differences in rotation of given coords.

        Parameters
        ----------
        coords : skrobot.coordinates.Coordinates
            given coordinates
        rotation_axis : str or bool or None (optional)
            we can take 'x', 'y', 'z', 'xx', 'yy', 'zz', 'xm', 'ym', 'zm',
            'xy', 'yx', 'yz', 'zy', 'zx', 'xz', True or False(None).

        Returns
        -------
        dif_rot : numpy.ndarray
            difference rotation of self coordinates and coords
            considering rotation_axis.

        Examples
        --------
        >>> from numpy import pi
        >>> from skrobot.coordinates import Coordinates
        >>> from skrobot.coordinates.math import rpy_matrix
        >>> coord1 = Coordinates()
        >>> coord2 = Coordinates(rot=rpy_matrix(pi / 2.0, pi / 3.0, pi / 5.0))
        >>> coord1.difference_rotation(coord2)
        array([-0.32855112,  1.17434985,  1.05738936])
        >>> coord1.difference_rotation(coord2, rotation_axis=False)
        array([0, 0, 0])
        >>> coord1.difference_rotation(coord2, rotation_axis='x')
        array([0.        , 1.36034952, 0.78539816])
        >>> coord1.difference_rotation(coord2, rotation_axis='y')
        array([0.35398131, 0.        , 0.97442695])
        >>> coord1.difference_rotation(coord2, rotation_axis='z')
        array([-0.88435715,  0.74192175,  0.        ])

        Using mirror option ['xm', 'ym', 'zm'], you can
        allow differences of mirror direction.

        >>> coord1 = Coordinates()
        >>> coord2 = Coordinates().rotate(pi, 'x')
        >>> coord1.difference_rotation(coord2, 'xm')
        array([-2.99951957e-32,  0.00000000e+00,  0.00000000e+00])
        >>> coord1 = Coordinates()
        >>> coord2 = Coordinates().rotate(pi / 2.0, 'x')
        >>> coord1.difference_rotation(coord2, 'xm')
        array([-1.57079633,  0.        ,  0.        ])
        """
        def need_mirror_for_nearest_axis(coords0, coords1, ax):
            a0 = coords0.axis(ax)
            a1 = coords1.axis(ax)
            a1_mirror = - a1
            dr1 = angle_between_vectors(a0, a1, normalize=False) \
                * normalize_vector(cross_product(a0, a1))
            dr1m = angle_between_vectors(a0, a1_mirror, normalize=False) \
                * normalize_vector(cross_product(a0, a1_mirror))
            return np.linalg.norm(dr1) < np.linalg.norm(dr1m)

        if rotation_axis in ['x', 'y', 'z']:
            a0 = self.axis(rotation_axis)
            a1 = coords.axis(rotation_axis)
            if np.abs(np.linalg.norm(np.array(a0) - np.array(a1))) < 0.001:
                dif_rot = np.array([0, 0, 0], 'f')
            else:
                dif_rot = np.matmul(
                    self.worldrot().T,
                    angle_between_vectors(a0, a1, normalize=False)
                    * normalize_vector(cross_product(a0, a1)))
        elif rotation_axis in ['xx', 'yy', 'zz']:
            ax = rotation_axis[0]
            a0 = self.axis(ax)
            a2 = coords.axis(ax)
            if not need_mirror_for_nearest_axis(self, coords, ax):
                a2 = - a2
            dif_rot = np.matmul(
                self.worldrot().T,
                angle_between_vectors(a0, a2, normalize=False)
                * normalize_vector(cross_product(a0, a2)))
        elif rotation_axis in ['xy', 'yx', 'yz', 'zy', 'zx', 'xz']:
            if rotation_axis in ['xy', 'yx']:
                ax1 = 'z'
                ax2 = 'x'
            elif rotation_axis in ['yz', 'zy']:
                ax1 = 'x'
                ax2 = 'y'
            else:
                ax1 = 'y'
                ax2 = 'z'
            a0 = self.axis(ax1)
            a1 = coords.axis(ax1)
            dif_rot = np.matmul(
                self.worldrot().T,
                angle_between_vectors(a0, a1, normalize=False)
                * normalize_vector(cross_product(a0, a1)))
            norm = np.linalg.norm(dif_rot)
            if np.isclose(norm, 0.0):
                self_coords = self.copy_worldcoords()
            else:
                self_coords = self.copy_worldcoords().rotate(norm, dif_rot)
            a0 = self_coords.axis(ax2)
            a1 = coords.axis(ax2)
            dif_rot = np.matmul(
                self_coords.worldrot().T,
                angle_between_vectors(a0, a1, normalize=False)
                * normalize_vector(cross_product(a0, a1)))
        elif rotation_axis in ['xm', 'ym', 'zm']:
            rot = coords.worldrot()
            ax = rotation_axis[0]
            if not need_mirror_for_nearest_axis(self, coords, ax):
                rot = rotate_matrix(rot, np.pi, ax)
            dif_rot = matrix_log(np.matmul(self.worldrot().T, rot))
        elif rotation_axis is False or rotation_axis is None:
            dif_rot = np.array([0, 0, 0])
        elif rotation_axis is True:
            dif_rotmatrix = np.matmul(self.worldrot().T,
                                      coords.worldrot())
            dif_rot = matrix_log(dif_rotmatrix)
        else:
            raise ValueError
        return dif_rot

    def rotate_with_matrix(self, mat, wrt='local'):
        """Rotate this coordinate by given rotation matrix.

        This is a subroutine of self.rotate function.

        Parameters
        ----------
        mat : numpy.ndarray
            rotation matrix shape of (3, 3)
        wrt : str or skrobot.coordinates.Coordinates
            with respect to.

        Returns
        -------
        self : skrobot.coordinates.Coordinates
        """
        if wrt == 'local' or wrt == self:
            rot = np.matmul(self._rotation, mat)
            self.newcoords(rot, self._translation, check_validity=False, relative_coords='local')
        elif wrt == 'parent' or wrt == self.parent or \
                wrt == 'world' or wrt is None or \
                wrt == worldcoords:
            rot = np.matmul(mat, self._rotation)
            self.newcoords(rot, self._translation, check_validity=False, relative_coords='local')
        elif isinstance(wrt, Coordinates):
            r2 = wrt.worldrot()
            r2t = r2.T
            r2t = np.matmul(mat, r2t)
            r2t = np.matmul(r2, r2t)
            self._rotation = np.matmul(r2t, self._rotation)
        else:
            raise ValueError('wrt {} is not supported'.format(wrt))
        return self

    def rotate(self, theta, axis=None, wrt='local', skip_normalization=False):
        """Rotate this coordinate by given theta and axis.

        This coordinate system is rotated relative to theta radians
        around the `axis` axis.
        Note that this function does not change a position of this coordinate.
        If you want to rotate this coordinates around with world frame,
        you can use `transform` function.
        Please see examples.

        Parameters
        ----------
        theta : float
            relartive rotation angle in radian.
        axis : str or None or numpy.ndarray
            axis of rotation.
            The value of `axis` is represented as `wrt` frame.
        wrt : str or skrobot.coordinates.Coordinates
        skip_normalization : bool
            if `True`, skip normalization for axis.

        Returns
        -------
        self : skrobot.coordinates.Coordinates

        Examples
        --------
        >>> from skrobot.coordinates import Coordinates
        >>> from numpy import pi
        >>> c = Coordinates()
        >>> c.translate((1.0, 0, 0))
        >>> c.rotate(pi / 2.0, 'z', wrt='local')
        >>> c.translation
        array([1., 0., 0.])

        >>> c.transform(Coordinates().rotate(np.pi / 2.0, 'z'), wrt='world')
        >>> c.translation
        array([0., 1., 0.])
        """
        if isinstance(axis, list) or isinstance(axis, np.ndarray):
            self.rotate_with_matrix(
                rotation_matrix(theta, axis,
                                skip_normalization=skip_normalization), wrt)
        elif axis is None or axis is False:
            self.rotate_with_matrix(theta, wrt)
        elif wrt == 'local' or wrt == self:
            self._rotation = rotate_matrix(
                self._rotation, theta, axis,
                skip_normalization=skip_normalization)
        elif wrt == 'parent' or wrt == 'world':
            self._rotation = rotate_matrix(
                self._rotation, theta,
                axis, True,
                skip_normalization=skip_normalization)
        elif isinstance(wrt, Coordinates):  # C1'=C2*R*C2(-1)*C1
            self.rotate_with_matrix(
                rotation_matrix(theta, axis,
                                skip_normalization=skip_normalization), wrt)
        else:
            raise ValueError('wrt {} not supported'.format(wrt))
        return self.newcoords(self._rotation, self._translation,
                              check_validity=False, relative_coords='local')

    def orient_with_matrix(self, rotation_matrix, wrt='world'):
        """Force update this coordinate system's rotation.

        Parameters
        ----------
        rotation_matrix : numpy.ndarray
            3x3 rotation matrix.
        wrt : str or skrobot.coordinates.Coordinates
            reference coordinates.
        """
        _check_valid_rotation(rotation_matrix)
        if wrt == 'local' or wrt == self:
            self._rotation = self._rotation.dot(rotation_matrix)
        elif wrt == 'world':
            self._rotation = rotation_matrix
        elif isinstance(wrt, Coordinates):
            self._rotation = wrt.worldrot().dot(rotation_matrix)
        else:
            raise TypeError('wrt {} not supported'.format(wrt))

    def copy(self):
        """Return a deep copy of the Coordinates."""
        return self.copy_coords()

    def copy_coords(self):
        """Return a deep copy of the Coordinates."""
        return Coordinates(pos=np.copy(self.worldpos()),
                           rot=np.copy(self.worldrot()),
                           check_validity=False)

    def coords(self):
        """Return a deep copy of the Coordinates."""
        return self.copy_coords()

    def worldcoords(self):
        """Return thisself"""
        if self._hook is not None:
            self._hook()
        return self

    def copy_worldcoords(self):
        """Return a deep copy of the Coordinates."""
        return self.coords()

    def worldrot(self):
        """Return rotation of this coordinate

        See also skrobot.coordinates.Coordinates.rotation

        Returns
        -------
        self._rotation : numpy.ndarray
            rotation matrix of this coordinate
        """
        return self._rotation

    def worldpos(self):
        """Return translation of this coordinate

        See also skrobot.coordinates.Coordinates.translation

        Returns
        -------
        self.translation : numpy.ndarray
            translation of this coordinate
        """
        return self._translation

    def newcoords(self, c, pos=None, check_validity=True,
                  relative_coords=None):
        """Update of coords is always done through newcoords.

        Parameters
        ----------
        c : skrobot.coordinates.Coordinates or numpy.ndarray
            If pos is None, c represents a Coordinates instance.
            If pos is given, c represents a rotation matrix.
        pos : numpy.ndarray or None
            New translation.
        check_validity : bool
            If True, check whether the input rotation and translation
            are valid.
        relative_coords : skrobot.coordinates.Coordinates or str or None
            Specifies the coordinate frame in which the input coordinates are expressed.

            - None or 'local': The input coordinates are treated as local coordinates.
              The coordinates are directly set without transformation.
              Example: coord.newcoords(c) directly sets coord to c's values.
              This is equivalent to: coord = c (for root coordinates)

              Note: If you want to set world coordinates, you have two options:

              1. Manual conversion (complex):
                 coord.newcoords(parent.worldcoords().inverse_transformation() * world_target)
              2. Use relative_coords='world' (recommended):
                 coord.newcoords(world_target, relative_coords='world')

            - 'world': The input coordinates are treated as world coordinates.
              They are transformed to the local frame before being set.
              Example: coord.newcoords(c, relative_coords='world') sets coord
              such that coord.worldcoords() equals c.

            - 'parent': The input coordinates are relative to the parent coordinate.
              Only meaningful for CascadedCoords with a parent.
              Example: child.newcoords(c, relative_coords='parent') sets child's
              position relative to its parent.

            - Coordinates instance: The input is relative to the given coordinate frame.
              Example: coord.newcoords(c, relative_coords=ref) sets coord such that
              ref.transform(c) becomes coord's world coordinates.

        Examples
        --------
        >>> from skrobot.coordinates import make_coords
        >>> coord = make_coords(pos=[1, 0, 0])
        >>>
        >>> # Direct assignment (default behavior)
        >>> new_c = make_coords(pos=[2, 2, 2])
        >>> coord.newcoords(new_c)
        >>> coord.translation
        array([2., 2., 2.])
        >>>
        >>> # World coordinate specification
        >>> coord = make_coords(pos=[1, 0, 0]).rotate(np.pi/2, 'z')
        >>> world_c = make_coords(pos=[3, 3, 3])
        >>> coord.newcoords(world_c, relative_coords='world')
        >>> coord.worldpos()  # Will be [3, 3, 3]
        array([3., 3., 3.])
        >>>
        >>> # Understanding the inverse_transformation relationship
        >>> parent = make_coords(pos=[5, 5, 5]).rotate(np.pi/4, 'z')
        >>> child = make_coords()
        >>> child.parent = parent  # Simulating parent-child relationship
        >>>
        >>> # To set child's world position to [10, 10, 10] using local coords:
        >>> world_target = make_coords(pos=[10, 10, 10])
        >>> local_coords = parent.inverse_transformation() * world_target
        >>> child.newcoords(local_coords)  # Sets local coords relative to parent
        >>> # Verify: parent * local_coords = world_target
        >>> (parent * child).worldpos()
        array([10., 10., 10.])
        """
        if relative_coords is not None:
            if isinstance(relative_coords, str):
                if relative_coords.lower() == 'parent':
                    if self.parent is None:
                        raise ValueError(
                            "No parent coordinate available for relative_coords='parent'")
                    relative_coords = self.parent
                elif relative_coords.lower() == 'world':
                    relative_coords = worldcoords
                elif relative_coords.lower() == 'local':
                    relative_coords = None
                else:
                    raise ValueError(
                        "Invalid value for relative_coords. "
                        + "Must be 'parent', 'world', or 'local'.")
            if relative_coords is not None:
                if pos is None:
                    c = relative_coords * c
                else:
                    temp = Coordinates(pos=pos, rot=c, check_validity=check_validity)
                    temp = relative_coords * temp
                    c = temp.rotation
                    pos = temp.translation

        if pos is not None:
            if check_validity:
                if id(self._rotation) != id(c):
                    self.rotation = copy.deepcopy(c)
                if id(self._translation) != id(pos):
                    self.translation = copy.deepcopy(pos)
            else:
                if id(self._rotation) != id(c):
                    self._rotation = np.copy(c)
                if id(self._translation) != id(pos):
                    self._translation = np.copy(pos)
        else:
            if check_validity:
                if id(self._rotation) != id(c._rotation):
                    self.rotation = copy.deepcopy(c._rotation)
                if id(self._translation) != id(c._translation):
                    self.translation = copy.deepcopy(c._translation)
            else:
                if id(self._rotation) != id(c._rotation):
                    self._rotation = np.copy(c._rotation)
                if id(self._translation) != id(c._translation):
                    self._translation = np.copy(c._translation)
        return self

    def __mul__(self, other_c):
        """Return Transformed Coordinates.

        Note that this function creates new Coordinates and
        does not change translation and rotation, unlike transform function.

        Parameters
        ----------
        other_c : skrobot.coordinates.Coordinates
            input coordinates.

        Returns
        -------
        out : skrobot.coordinates.Coordinates
            transformed coordinates multiplied other_c from the right.
            T = T_{self} T_{other_c}.
        """
        return transform_coords(self, other_c)

    def __pow__(self, exponent):
        """Return exponential homogeneous matrix.

        If exponent equals -1, return inverse transformation of this coords.

        Parameters
        ----------
        exponent : numbers.Number
            exponent value.
            If exponent equals -1, return inverse transformation of this
            coords.
            In current, support only -1 case.

        Returns
        -------
        out : skrobot.coordinates.Coordinates
            output.
        """
        if np.isclose(exponent, -1):
            return self.inverse_transformation()
        raise NotImplementedError

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        self.worldrot()
        pos = self.worldpos()
        self.rpy = matrix2ypr(self._rotation)
        if self.name:
            prefix = self.__class__.__name__ + ':' + self.name
        else:
            prefix = self.__class__.__name__

        return "#<{0} {1} "\
            "{2:.3f} {3:.3f} {4:.3f} / {5:.1f} {6:.1f} {7:.1f}>".\
            format(prefix,
                   hex(id(self)),
                   pos[0],
                   pos[1],
                   pos[2],
                   self.rpy[0],
                   self.rpy[1],
                   self.rpy[2])


class CascadedCoords(Coordinates):

    def __init__(self, parent=None, *args, **kwargs):
        super(CascadedCoords, self).__init__(*args, **kwargs)
        self.manager = self
        self._changed = True
        self._descendants = []

        self._worldcoords = Coordinates(pos=self.translation,
                                        rot=self._rotation,
                                        hook=self.update,
                                        check_validity=False)

        self.parent = parent
        if parent is not None:
            # Because we must self.parent = parent in this case,
            # force=True is required.
            parent.assoc(self, force=True)

    @property
    def descendants(self):
        return self._descendants

    def assoc(self, child, relative_coords='world', force=False,
              **kwargs):
        """Associate child coords to this coordinate system.

        If `relative_coords` is `None` or 'world', the translation and rotation
        of childcoords in the world coordinate system do not change.
        If `relative_coords` is specified, childcoords is assoced
        at translation and rotation of `relative_coords`.
        By default, if child is already assoced to some other coords,
        raise an exception. But if `force` is `True`, you can overwrite
        the existing assoc relation.

        Parameters
        ----------
        child : CascadedCoords
            child coordinates.
        relative_coords : None or Coordinates or str
            child coordinates' relative coordinates.
        force : bool
            predicate for overwriting the existing assoc-relation

        Returns
        -------
        child : CascadedCoords
            assoced child.

        Examples
        --------
        >>> from skrobot.coordinates import CascadedCoords
        >>> parent_coords = CascadedCoords(pos=[1, 0, 0])
        >>> child_coords = CascadedCoords(pos=[1, 1, 0])
        >>> parent_coords.assoc(child_coords)
        #<CascadedCoords 0x7f1d30e29510 1.000 1.000 0.000 / 0.0 -0.0 0.0>
        >>> child_coords.worldpos()
        array([1., 1., 0.])
        >>> child_coords.translation
        array([0., 1., 0.])

        `None` and 'world' have the same meaning.

        >>> parent_coords = CascadedCoords(pos=[1, 0, 0])
        >>> child_coords = CascadedCoords(pos=[1, 1, 0])
        >>> parent_coords.assoc(child_coords, relative_coords='world')
        #<CascadedCoords 0x7f1d30e29510 1.000 1.000 0.000 / 0.0 -0.0 0.0>
        >>> child_coords.worldpos()
        array([1., 1., 0.])

        If `relative_coords` is 'local', `child` is associated at
        world translation and world rotation of `child` from this coordinate
        system.

        >>> parent_coords = CascadedCoords(pos=[1, 0, 0])
        >>> child_coords = CascadedCoords(pos=[1, 1, 0])
        >>> parent_coords.assoc(child_coords, relative_coords='local')
        >>> child_coords.worldpos()
        array([2., 1., 0.])
        >>> child_coords.translation
        array([1., 1., 0.])
        """
        if 'c' in kwargs:
            warnings.warn(
                'Argument `c` is deprecated. '
                'Please use `relative_coords` instead',
                DeprecationWarning)
            relative_coords = kwargs['c']

        if self == child:
            msg = "Cannot associate a coordinate system with itself. " \
                  "A coordinate system cannot be both parent and child. " \
                  "Please ensure 'child' is a distinct object from 'self'."
            raise ValueError(msg)
        is_invalid_assoc = (child.parent is not None) and (not force)
        if is_invalid_assoc:
            msg = "child already has an assoc relation with '{0}'."\
                " To overwrite this, please specify force=True."\
                .format(child.parent.name)
            raise RuntimeError(msg)

        if child not in self._descendants:
            if relative_coords is None or relative_coords == 'world':
                relative_coords = self.worldcoords().transformation(
                    child.worldcoords())
            elif relative_coords == 'local':
                relative_coords = child.worldcoords()
            elif not isinstance(relative_coords, Coordinates):
                raise TypeError(
                    "`relative_coords`'s type should be"
                    "skrobot.coordinates.Coordinates, but is {}"
                    .format(type(relative_coords)))
            child.parent = self
            child.newcoords(relative_coords, check_validity=False, relative_coords='local')
            self._descendants.append(child)
        return child

    def dissoc(self, child):
        if child in self._descendants:
            c = child.worldcoords().copy_coords()
            self._descendants.remove(child)
            child.parent = None
            child.newcoords(c, check_validity=False, relative_coords='local')

    def newcoords(self, target, pos=None, check_validity=True,
                  relative_coords='local'):
        """Update this coordinate system with a new coordinate value.

        This method updates the coordinates while maintaining the parent-child relationship.
        The key difference from Coordinates.newcoords is that this method handles the
        parent-child transformation automatically.

        Parameters
        ----------
        target : skrobot.coordinates.Coordinates or numpy.ndarray
            If pos is None, target represents a Coordinates instance
            that describes the new desired coordinate.
            If pos is provided, target represents a rotation matrix.
        pos : numpy.ndarray or None
            The new translation vector.
        check_validity : bool
            Whether to validate the inputs.
        relative_coords : str or skrobot.coordinates.Coordinates, default 'local'
            Specifies the coordinate frame in which the target coordinates are expressed.

            - 'local' (default): The target represents coordinates in the local frame
              (relative to parent if it exists). This is the default for backward compatibility.
              Example: child.newcoords(c) directly sets child's local transformation to c.
              For a child with parent, this means: child = parent * c (in world frame)

              Note: If you want to set world coordinates, you have two options:

              1. Manual conversion (complex):
                 child.newcoords(parent.worldcoords().inverse_transformation() * world_target)
              2. Use relative_coords='world' (recommended):
                 child.newcoords(world_target, relative_coords='world')

            - 'world': The target represents desired world coordinates.
              For child coordinates, the target is automatically converted to the
              parent's local frame to maintain the correct world position.
              Example: child.newcoords(c, relative_coords='world') sets child
              such that child.worldcoords() equals c.
            - 'local': the target is already expressed in the child's local frame.
            - 'parent': the target is given relative to the parent coordinate.
            - Alternatively, a Coordinates instance can be provided as the reference frame.

        Returns
        -------
        self : CascadedCoords

        Examples
        --------
        >>> from skrobot.coordinates import make_cascoords
        >>> parent = make_cascoords(pos=[10, 0, 0])
        >>> child = make_cascoords(pos=[0, 5, 0])
        >>> parent.assoc(child)
        >>>
        >>> # Setting world coordinates
        >>> target_world = make_cascoords(pos=[15, 15, 15])
        >>> child.newcoords(target_world, relative_coords='world')
        >>> child.worldpos()
        array([15., 15., 15.])
        >>> child.translation  # Local position relative to parent
        array([5., 15., 15.])
        >>>
        >>> # Setting local coordinates
        >>> target_local = make_cascoords(pos=[2, 2, 2])
        >>> child.newcoords(target_local, relative_coords='local')
        >>> child.translation
        array([2., 2., 2.])
        >>> child.worldpos()  # World position is parent + local
        array([12., 2., 2.])
        >>>
        >>> # Manual world coordinate setting using inverse_transformation
        >>> world_target = make_cascoords(pos=[20, 20, 20])
        >>> local_target = parent.worldcoords().inverse_transformation() * world_target
        >>> child.newcoords(local_target)  # Default is 'local'
        >>> child.worldpos()
        array([20., 20., 20.])
        """
        if self.parent is not None:
            if isinstance(relative_coords, str):
                if relative_coords.lower() == 'world':
                    if pos is None:
                        target = self.parent.worldcoords().inverse_transformation().transform(target)
                    else:
                        temp = Coordinates(pos=pos, rot=target, check_validity=check_validity)
                        temp = self.parent.worldcoords().inverse_transformation().transform(temp)
                        target = temp.rotation
                        pos = temp.translation
                elif relative_coords.lower() == 'local':
                    pass
                elif relative_coords.lower() == 'parent':
                    pass
                else:
                    raise ValueError(
                        "Invalid relative_coords value. "
                        "Use 'world', 'local', 'parent', or provide a Coordinates instance.")
            elif isinstance(relative_coords, Coordinates):
                if pos is None:
                    target = relative_coords.transformation(target)
                else:
                    temp = Coordinates(pos=pos, rot=target, check_validity=check_validity)
                    temp = relative_coords.transformation(temp)
                    target = temp.rotation
                    pos = temp.translation
            else:
                raise TypeError("relative_coords must be a string "
                                "('world', 'local', or 'parent') or a Coordinates instance.")
        super(CascadedCoords, self).newcoords(target, pos, check_validity, relative_coords=None)
        self.changed()
        return self

    def changed(self):
        if self._changed is False:
            self._changed = True
            return [c.changed() for c in self._descendants]
        return [False]

    def parentcoords(self):
        if self.parent:
            return self.parent.worldcoords()
        return worldcoords

    def transform_vector(self, v):
        return self.worldcoords().transform_vector(v)

    def inverse_transform_vector(self, v):
        return self.worldcoords().inverse_transform_vector(v)

    def rotate_with_matrix(self, matrix, wrt):
        if wrt == 'local' or wrt == self:
            self._rotation = np.dot(self._rotation, matrix)
            return self.newcoords(self._rotation, self._translation,
                                  check_validity=False, relative_coords='local')
        elif wrt == 'parent' or wrt == self.parent:
            rotation = np.matmul(matrix, self._rotation)
            return self.newcoords(
                rotation, self._translation, check_validity=False, relative_coords='local')
        else:
            parent_coords = self.parentcoords()
            parent_rot = parent_coords._rotation
            if isinstance(wrt, Coordinates):
                wrt_rot = wrt.worldrot()
                matrix = np.matmul(wrt_rot, matrix)
                matrix = np.matmul(matrix, wrt_rot.T)
            matrix = np.matmul(matrix, parent_rot)
            matrix = np.matmul(parent_rot.T, matrix)
            rotation = np.matmul(matrix, self._rotation)
            return self.newcoords(rotation, self._translation,
                                  check_validity=False, relative_coords='local')

    def rotate(self, theta, axis, wrt='local', skip_normalization=False):
        """Rotate this coordinate.

        Rotate this coordinate relative to axis by theta radians
        with respect to wrt.

        Parameters
        ----------
        theta : float
            radian
        axis : str or numpy.ndarray
            'x', 'y', 'z' or vector
        wrt : str or Coordinates
        skip_normalization : bool
            if `True`, skip normalization for axis.

        Returns
        -------
        self
        """
        if isinstance(axis, list) or isinstance(axis, np.ndarray):
            return self.rotate_with_matrix(
                rotation_matrix(theta, axis,
                                skip_normalization=skip_normalization), wrt)
        if isinstance(axis, np.ndarray) and axis.shape == (3, 3):
            return self.rotate_with_matrix(theta, wrt)

        if wrt == 'local' or wrt == self:
            rotation = rotate_matrix(self._rotation, theta, axis,
                                     skip_normalization=skip_normalization)
            return self.newcoords(rotation, self._translation,
                                  check_validity=False, relative_coords='local')
        elif wrt == 'parent' or wrt == self.parent:
            rotation = rotate_matrix(self._rotation, theta, axis,
                                     skip_normalization=skip_normalization)
            return self.newcoords(rotation, self._translation,
                                  check_validity=False, relative_coords='local')
        else:
            return self.rotate_with_matrix(
                rotation_matrix(theta, axis,
                                skip_normalization=skip_normalization), wrt)

    def orient_with_matrix(self, rotation_matrix, wrt='world'):
        """Force update this coordinate system's rotation.

        Parameters
        ----------
        rotation_matrix : numpy.ndarray
            3x3 rotation matrix.
        wrt : str or skrobot.coordinates.Coordinates
            reference coordinates.
        """
        _check_valid_rotation(rotation_matrix)
        if wrt == 'local' or wrt == self:
            rotation = self._rotation.dot(rotation_matrix)
        elif wrt == 'parent' or wrt == self.parent:
            rotation = rotation_matrix
        elif wrt == 'world':
            # R_{input} = R_{world} = R_{parent} R_{this}
            # R_{this} = R_{parent}^{-1} R_{input}
            parent_worldcoords = self.parentcoords()
            rotation = parent_worldcoords._rotation.T.dot(rotation_matrix)
        elif isinstance(wrt, Coordinates):
            # R_{world} = R_{wrt} R_{input}
            # R_{world} = R_{parent} R_{this}
            # R_{this} = R_{parent}^{-1} R_{world}
            # R_{this} = R_{parent}^{-1} R_{world} R_{wrt} R_{input}
            world_rotation_matrix = wrt.worldrot().dot(rotation_matrix)
            parent_worldcoords = self.parentcoords()
            rotation = parent_worldcoords._rotation.T.dot(
                world_rotation_matrix)
        else:
            raise TypeError('wrt {} not supported'.format(wrt))
        return self.newcoords(rotation, self._translation,
                              check_validity=False, relative_coords='local')

    def rotate_vector(self, v):
        return self.worldcoords().rotate_vector(v)

    def inverse_rotate_vector(self, v):
        return self.worldcoords().inverse_rotate_vector(v)

    def transform(self, c, wrt='local', out=None):
        """Transform this coordinates

        Parameters
        ----------
        c : skrobot.coordinates.Coordinates
            coordinates
        wrt : str or skrobot.coordinates.Coordinates
            transform this coordinates with respect to wrt.
            If wrt is 'local' or self, multiply c from the right.
            If wrt is 'parent' or self.parent, transform c
            with respect to parentcoords. (multiply c from the left.)
            If wrt is Coordinates, transform c with respect to c.
        out : None or skrobot.coordinates.Coordinates
            If the `out` is specified, set new coordinates to `out`.
            Note that if the `out` is given, these coordinates don't change.

        Returns
        -------
        self : skrobot.coordinates.CascadedCoords
            return self
        """
        if out is None:
            out = self
        if isinstance(wrt, Coordinates):
            transform_coords(self.parentcoords(), self, out)
            transform_coords(wrt.inverse_transformation(), out, out)
            transform_coords(c, out, out)
            transform_coords(wrt.worldcoords(), out, out)
            transform_coords(self.parentcoords().inverse_transformation(),
                             out, out)
        elif wrt == 'local' or wrt == self:
            # multiply c from the right.
            transform_coords(self, c, out)
        elif wrt == 'parent' or wrt == self.parent:
            # multiply c from the left.
            transform_coords(c, self, out)
        elif wrt == 'world':
            parentcoords = self.parentcoords()
            transform_coords(parentcoords, self, out)
            transform_coords(c, out, out)
            transform_coords(parentcoords.inverse_transformation(),
                             out, out)
        else:
            raise ValueError('transform wrt {} is not supported'.format(wrt))
        return out.newcoords(out._rotation, out._translation,
                             check_validity=False, relative_coords='local')

    def update(self, force=False):
        if not force and not self._changed:
            return
        hook_disabled, original_hook = self.disable_hook()
        try:
            if self._parent:
                transform_coords(
                    self._parent.worldcoords(),
                    self,
                    self._worldcoords)
            else:
                self._worldcoords._rotation = self._rotation
                self._worldcoords._translation = self._translation
        finally:
            if hook_disabled:
                self._hook = original_hook
        self._changed = False

    def worldcoords(self):
        """Calculate rotation and position in the world."""
        self.update()
        return self._worldcoords

    def worldrot(self):
        return self.worldcoords()._rotation

    def worldpos(self):
        return self.worldcoords()._translation

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, c):
        if not (c is None or coordinates_p(c)):
            raise ValueError('parent should be None or Coordinates. '
                             'get type=={}'.format(type(c)))
        self._parent = c

    def __getstate__(self):
        assert self._worldcoords._hook == self.update
        d = self.__dict__.copy()

        is_python3 = sys.version_info.major > 2
        if is_python3:
            # NOTE: the following deepcopy is costly. For example,
            # copying raw CascadedCoords with the following procedure
            # makes pickling 2x slower. Thus, we don't do that if python3.
            return d
        else:
            # NOTE: setting self._worldcoords._hook = None before deepcopy
            # is important. Without this, infinite recursion will occur
            # because self._worldcoords._hook is otherwise a method
            # of CascadedCoords.
            self._worldcoords._hook = None
            d["_worldcoords"] = copy.deepcopy(self._worldcoords)
            d["_worldcoords"].__setattr__("_hook", None)

            # recover the original _hook
            self._worldcoords._hook = self.update
            return d

    def __setstate__(self, d):
        self.__dict__ = d
        is_python3 = sys.version_info.major > 2
        if is_python3:
            return
        else:
            assert self._worldcoords._hook is None  # as we set in __getstate__
            self._worldcoords._hook = self.update  # register again
            assert self._worldcoords._hook == self.update


def coordinates_p(x):
    """Return whether an object is an instance of a class or of a subclass"""
    return isinstance(x, Coordinates)


def make_coords(*args, **kwargs):
    """Return Coordinates

    This is a wrapper of Coordinates class
    """
    return Coordinates(*args, **kwargs)


def make_cascoords(*args, **kwargs):
    """Return CascadedCoords

    This is a wrapper of CascadedCoords
    """
    return CascadedCoords(*args, **kwargs)


def random_coords():
    """Return Coordinates class has random translation and rotation"""
    return Coordinates(pos=random_translation(),
                       rot=random_rotation())


def wrt(coords, vec):
    return coords.transform_vector(vec)


def coordinates_distance(c1, c2, c=None):
    if c is None:
        c = c1.transformation(c2)
    return np.linalg.norm(c.worldpos()), rotation_angle(c.worldrot())[0]


def slerp_coordinates(c1, c2, t):
    """Spherical linear interpolation between two coordinates.

    Performs spherical linear interpolation (SLERP) between two coordinate frames,
    interpolating both position and orientation smoothly using true quaternion SLERP.

    Parameters
    ----------
    c1 : skrobot.coordinates.Coordinates
        Starting coordinate frame
    c2 : skrobot.coordinates.Coordinates
        Ending coordinate frame
    t : float
        Interpolation parameter (0.0 = c1, 1.0 = c2)

    Returns
    -------
    result : skrobot.coordinates.Coordinates
        Interpolated coordinate frame

    Examples
    --------
    >>> from skrobot.coordinates import Coordinates
    >>> from skrobot.coordinates.base import slerp_coordinates
    >>> import numpy as np
    >>> c1 = Coordinates(pos=[0, 0, 0])
    >>> c2 = Coordinates(pos=[1, 1, 1]).rotate(np.pi/2, 'z')
    >>> c_mid = slerp_coordinates(c1, c2, 0.5)
    >>> c_mid.translation
    array([0.5, 0.5, 0.5])
    """
    if not (0.0 <= t <= 1.0):
        raise ValueError("Interpolation parameter t must be between 0.0 and 1.0")

    # Linear interpolation for translation
    pos1 = c1.worldpos()
    pos2 = c2.worldpos()
    interpolated_pos = pos1 + t * (pos2 - pos1)

    # True spherical linear interpolation for rotation using quaternions
    q1 = c1.quaternion
    q2 = c2.quaternion

    # Ensure we take the shorter path for rotation
    if np.dot(q1, q2) < 0:
        q2 = -q2

    # Compute the angle between quaternions
    dot_product = np.clip(np.dot(q1, q2), -1.0, 1.0)
    omega = np.arccos(np.abs(dot_product))

    if np.abs(omega) < 1e-6:
        # Linear interpolation for very small angles (avoid division by zero)
        slerp_q = (1 - t) * q1 + t * q2
    else:
        sin_omega = np.sin(omega)
        slerp_q = (np.sin((1 - t) * omega) / sin_omega) * q1 + (np.sin(t * omega) / sin_omega) * q2

    slerp_q = slerp_q / np.linalg.norm(slerp_q)
    interpolated_rot = quaternion2matrix(slerp_q)
    result = Coordinates(pos=interpolated_pos, rot=interpolated_rot, check_validity=False)
    return result


def lerp_coordinates(c1, c2, t):
    """Linear interpolation between two coordinates.

    Performs linear interpolation between two coordinate frames for both
    position and orientation. Uses quaternion LERP for rotation to avoid
    artifacts from matrix interpolation.

    Parameters
    ----------
    c1 : skrobot.coordinates.Coordinates
        Starting coordinate frame
    c2 : skrobot.coordinates.Coordinates
        Ending coordinate frame
    t : float
        Interpolation parameter (0.0 = c1, 1.0 = c2)

    Returns
    -------
    result : skrobot.coordinates.Coordinates
        Interpolated coordinate frame

    Examples
    --------
    >>> from skrobot.coordinates import Coordinates
    >>> from skrobot.coordinates.base import lerp_coordinates
    >>> c1 = Coordinates(pos=[0, 0, 0])
    >>> c2 = Coordinates(pos=[2, 2, 2])
    >>> c_mid = lerp_coordinates(c1, c2, 0.5)
    >>> c_mid.translation
    array([1., 1., 1.])
    """
    if not (0.0 <= t <= 1.0):
        raise ValueError("Interpolation parameter t must be between 0.0 and 1.0")

    # Linear interpolation for translation
    pos1 = c1.worldpos()
    pos2 = c2.worldpos()
    interpolated_pos = pos1 + t * (pos2 - pos1)

    # Linear interpolation for rotation using quaternions
    q1 = c1.quaternion
    q2 = c2.quaternion

    # Ensure we take the shorter path
    if np.dot(q1, q2) < 0:
        q2 = -q2

    # Linear interpolation of quaternions
    lerp_q = (1 - t) * q1 + t * q2

    lerp_q = lerp_q / np.linalg.norm(lerp_q)
    interpolated_rot = quaternion2matrix(lerp_q)
    result = Coordinates(pos=interpolated_pos, rot=interpolated_rot, check_validity=False)
    return result


worldcoords = CascadedCoords(name='worldcoords')
