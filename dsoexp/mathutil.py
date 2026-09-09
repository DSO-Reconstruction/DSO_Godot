"""Small transform helpers, on numpy."""
import numpy as np


def quat_to_mat(q):
    """Quaternion (x, y, z, w) -> 3x3 rotation matrix."""
    x, y, z, w = q[0], q[1], q[2], q[3]
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array([
        [1.0 - (yy + zz), xy - wz, xz + wy],
        [xy + wz, 1.0 - (xx + zz), yz - wx],
        [xz - wy, yz + wx, 1.0 - (xx + yy)],
    ])


def compose(translation, rotation, scale):
    """T * R * S -> 4x4 matrix."""
    m = np.eye(4)
    m[:3, :3] = quat_to_mat(rotation) @ np.diag([scale[0], scale[1], scale[2]])
    m[:3, 3] = translation[:3]
    return m


def normalize_quat(q):
    n = (q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2) ** 0.5
    return (0.0, 0.0, 0.0, 1.0) if n < 1e-9 else (q[0] / n, q[1] / n, q[2] / n, q[3] / n)
