import sys
import grpc
import dispatch_pb2
import dispatch_pb2_grpc

if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python debug_dispatch.py <node_b_host> <node_b_port> <query_text>")
        sys.exit(2)

    host = sys.argv[1]
    port = int(sys.argv[2])
    query_text = sys.argv[3]
    target = f"{host}:{port}"

    channel = grpc.insecure_channel(target)
    stub = dispatch_pb2_grpc.DenseDispatcherStub(channel)

    req = dispatch_pb2.DenseDispatchRequest(
        query_id="dbg1",
        query_text=query_text,
        top_k=5,
        node_a_lan_host="10.8.0.1",
        node_a_grpc_port=50052,
    )

    try:
        resp = stub.Dispatch(req, timeout=10.0)
        print("Response:", resp)
    except grpc.RpcError as e:
        print("gRPC error:", e.code(), e.details())
        raise
