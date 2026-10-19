import numpy as np
import pytest

from floodline.segeval import boundary_f1, dice, iou, score_pair


def square(h=40, w=40, y0=10, y1=30, x0=10, x1=30):
    m = np.zeros((h, w), bool)
    m[y0:y1, x0:x1] = True
    return m


def test_identical_masks_score_one():
    m = square()
    assert score_pair(m, m) == {"iou": 1.0, "dice": 1.0, "boundary_f1": 1.0}


def test_disjoint_masks_score_zero():
    a, b = square(x0=0, x1=10), square(x0=20, x1=30)
    assert iou(a, b) == 0.0 and dice(a, b) == 0.0 and boundary_f1(a, b) == 0.0


def test_half_overlap_values():
    a = square(x0=0, x1=20)
    b = square(x0=10, x1=30)
    assert iou(a, b) == pytest.approx(1 / 3)
    assert dice(a, b) == pytest.approx(0.5)


def test_empty_vs_empty_is_perfect_and_empty_vs_full_is_zero():
    empty = np.zeros((8, 8), bool)
    assert iou(empty, empty) == 1.0
    assert boundary_f1(empty, empty) == 1.0
    assert boundary_f1(empty, square(8, 8, 2, 6, 2, 6)) == 0.0


def test_boundary_f1_tolerates_one_pixel_shift():
    a = square(x0=10, x1=30)
    b = square(x0=11, x1=31)
    assert boundary_f1(a, b, tol=2) == 1.0
    assert boundary_f1(a, b, tol=0) < 1.0
