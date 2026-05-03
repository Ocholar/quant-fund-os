def walk_forward(data, train_size=252, test_size=63):
    out, start = [], 0
    while start + train_size + test_size < len(data):
        train = data[start:start+train_size]
        test = data[start+train_size:start+train_size+test_size]
        out.append({"train_rows": len(train), "test_rows": len(test)})
        start += test_size
    return out
