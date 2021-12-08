from vertex import *


class TestCheckRationality:

    def __init__(self):
        self.test_vertices = None

    def setup(self):
        from vertex import Vertex
        test_vertex1 = Vertex(marginal_price=-1,
                              prod_cost=0,
                              power=-100)
        test_vertex2 = Vertex(0, 0, 0)
        test_vertex3 = Vertex(1, 0, 100)
        self.test_vertices = [test_vertex1, test_vertex2, test_vertex3]

    def case1(self):
        print('\n  Case 1: Set of three rational vertices.')
        self.setup()
        test_object = self.test_vertices
        output_vertices = []
        count = None
        try:
            [output_vertices, count] = Vertex.check_rationality(test_object)
            print('  - The method ran without errors.')
        except RuntimeError as err:
            print('  - ERRORS WERE ENCOUNTERED: ', err)
        assert test_object == output_vertices, "The output vertices were not the same as the input vertices."
        assert count == 0, "The count of irrational vertices was not 0 as expected."

    def case2(self):
        print('\n  Case 2: Count one irrational vertex.')
        self.setup()
        self.test_vertices[1].power = self.test_vertices[0].power-1   # making one vertex power irrational
        test_object = self.test_vertices
        output_vertices = []
        count = None
        try:
            [output_vertices, count] = Vertex.check_rationality(test_object)
            print('  - The method ran without errors.')
        except RuntimeError as err:
            print('  - ERRORS WERE ENCOUNTERED: ', err)
        assert test_object == output_vertices, "The output vertices were not the same as the input vertices."
        assert count == 1, "The count of irrational vertices was not 1 as expected."

    def case3(self):
        print('\n  Case 3: Use fix_up action to fix one irrational vertex.')
        self.setup()
        self.test_vertices[1].power = self.test_vertices[0].power - 1  # making one vertex power irrational
        test_object = self.test_vertices
        output_vertices = []
        count = None
        try:
            [output_vertices, count] = Vertex.check_rationality(test_object,
                                                                action="fix_up")
            print('  - The method ran without errors.')
        except RuntimeError as err:
            print('  - ERRORS WERE ENCOUNTERED: ', err)
        assert test_object == output_vertices, "An output vertex object identities should remain the same."
        assert len(output_vertices) == len(test_object), "The count of vertices should not have changed."
        assert output_vertices[1].power == output_vertices[0].power, "The irrational power was not corrected properly"
        assert count == 1, "The count of irrational vertices was not 1 as expected."

    def case4(self):
        print('\n  Case 4: Use delete_up action to fix one irrational vertex.')
        self.setup()
        self.test_vertices[1].power = self.test_vertices[0].power - 1  # making one vertex power irrational
        test_object = self.test_vertices
        output_vertices = []
        count = None
        try:
            [output_vertices, count] = Vertex.check_rationality(test_object,
                                                                action="delete_up")
            print('  - The method ran without errors.')
        except RuntimeError as err:
            print('  - ERRORS WERE ENCOUNTERED: ', err)
        assert test_object != output_vertices, "An output vertex object identities should have changed."
        assert len(output_vertices) == 2, "One irrational vertex should have been deleted, leaving 2."
        assert count == 1, "The count of irrational vertices was not 1 as expected."

    def case5(self):
        print('\n  Case 5: Use fix_down action to fix one irrational vertex.')
        self.setup()
        self.test_vertices[1].power = self.test_vertices[0].power - 1  # making one vertex power irrational
        test_object = self.test_vertices
        output_vertices = []
        count = None
        try:
            [output_vertices, count] = Vertex.check_rationality(test_object,
                                                                action="fix_down")
            print('  - The method ran without errors.')
        except RuntimeError as err:
            print('  - ERRORS WERE ENCOUNTERED: ', err)
        assert test_object == output_vertices, "An output vertex object identities should remain the same."
        assert len(output_vertices) == len(test_object), "The count of vertices should not have changed."
        assert output_vertices[0].power == output_vertices[1].power, "The irrational power was not corrected properly"
        assert count == 1, "The count of irrational vertices was not 1 as expected."

    def case6(self):
        print('\n  Case 6: Use delete_down action to fix one irrational vertex.')
        self.setup()
        self.test_vertices[1].power = self.test_vertices[0].power - 1  # making one vertex power irrational
        test_object = self.test_vertices
        output_vertices = []
        count = None
        try:
            [output_vertices, count] = Vertex.check_rationality(test_object,
                                                                action="delete_down")
            print('  - The method ran without errors.')
        except RuntimeError as err:
            print('  - ERRORS WERE ENCOUNTERED: ', err)
        assert test_object != output_vertices, "An output vertex object identities should have changed."
        assert len(output_vertices) == 2, "One irrational vertex should have been deleted, leaving 2."
        assert count == 1, "The count of irrational vertices was not 1 as expected."


if __name__ == "__main__":
    print('\nRunning test_vertex.py.')
    test_fixture = TestCheckRationality()
    print('  Running test fixture ' + str(test_fixture) + '.')
    test_fixture.case1()
    test_fixture.case2()
    test_fixture.case3()
    test_fixture.case4()
    test_fixture.case5()
    test_fixture.case6()
    print('\ntest_vertex.py ran to completion.')
