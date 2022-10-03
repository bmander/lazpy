from lazpy import Reader
import sys


def read_txtfile_entries(filename):
    """Read a text file and return a list of lines."""
    with open(filename, 'r') as f:
        for line in f:
            yield [int(x) for x in line.split()]


def fast_forward(iterable, n):
    for i in range(n):
        next(iterable)


def main(filename, txtpoints_filename):

    print("Opening file: {}".format(filename))

    reader = Reader()

    reader.open(filename)

    print("num points: ", reader.num_points)

    target_point_index = 0
    chunk_index = target_point_index // reader.chunk_size

    i_start = chunk_index*reader.chunk_size

    entries = read_txtfile_entries(txtpoints_filename)

    if chunk_index > 0:
        print(f"fast forwarding to desired point to i:{i_start} "
              f"chunk:{chunk_index}")
        fast_forward(entries, i_start)
        reader.jump_to_chunk(chunk_index)

    for i, entry in zip(range(i_start, reader.num_points), entries):

        try:
            point = reader.read()
        except Exception as e:
            print("error at point: ", i)
            raise e

        comp = [i, point[0].x, point[0].y, point[0].z, point[0].intensity,
                point[1]]

        if comp != entry:
            print("mismatch at ", i)
            print("us", comp)
            print("them", entry)
            exit()

        if i % 1000 == 0:
            print(i, ":", [str(x) for x in point])


if __name__ == '__main__':

    # get first command line argument
    if len(sys.argv) > 2:
        filename = sys.argv[1]
        pointstxt = sys.argv[2]
    else:
        print("Usage: pylaszip.py filename.laz points.txt")
        sys.exit(1)

    main(filename, pointstxt)
