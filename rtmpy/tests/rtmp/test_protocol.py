# Copyright the RTMPy project.
# See LICENSE.txt for details.

"""
Tests for L{rtmpy.protocol.rtmp}
"""

from twisted.trial import unittest
from twisted.internet import error, defer, task
from twisted.test.proto_helpers import StringTransportWithDisconnection

from rtmpy.protocol import rtmp


class MockHandshakeNegotiator(object):
    """
    """

    def __init__(self, test, protocol):
        self.test = test
        self.procotol = protocol
        self.started = False

        self.data = None

    def start(self, uptime, version):
        self.started = True

    def dataReceived(self, data):
        if self.data is None:
            self.data = data
        else:
            self.data += data


class MockFactory(object):
    """
    """

    def __init__(self, test, protocol):
        self.test = test
        self.protocol = protocol

    def buildHandshakeNegotiator(self, protocol):
        self.test.assertIdentical(protocol, self.protocol)

        return self.test.handshaker


class MockApplication(object):
    """
    """

    def __init__(self, test, protocol):
        self.test = test
        self.protocol = protocol

    def clientDisconnected(self, client, reason):
        self.test.assertIdentical(client, self.protocol)


class ProtocolTestCase(unittest.TestCase):
    """
    """

    def setUp(self):
        self.protocol = rtmp.RTMPProtocol()
        self.handshaker = MockHandshakeNegotiator(self, self.protocol)
        self.transport = self.protocol.transport = StringTransportWithDisconnection()
        self.transport.protocol = self.protocol

    def connect(self):
        self.protocol.factory = MockFactory(self, self.protocol)
        self.protocol.connectionMade()


class StateTest(ProtocolTestCase):
    """
    Test protocol state between not connected/handshaking/streaming/disconnected
    """

    def test_not_connected(self):
        self.assertFalse(hasattr(self.protocol, 'state'))

    def test_connect(self):
        self.connect()

        self.assertEqual(self.protocol.state, 'handshake')
        self.assertTrue(self.handshaker.started)

        self.assertIdentical(self.protocol.handshaker, self.handshaker)

    def test_stream(self):
        self.connect()
        self.protocol.handshakeSuccess('')

        self.assertEqual(self.protocol.state, 'stream')
        self.assertFalse(hasattr(self.protocol, 'handshaker'))
        self.assertEqual(self.protocol.streams, {})
        self.assertEquals(self.protocol.application, None)


class ConnectionLostTestCase(ProtocolTestCase):
    """
    Tests for losing connection at various states of the protocol
    """

    def setUp(self):
        ProtocolTestCase.setUp(self)

        self.connect()

    def test_handshake(self):
        self.protocol.connectionLost(error.ConnectionDone())

        self.assertFalse(hasattr(self.protocol, 'handshaker'))

    def test_stream(self):
        self.protocol.handshakeSuccess('')

        self.protocol.connectionLost(error.ConnectionDone())

        self.assertFalse(hasattr(self.protocol, 'decoder'))
        self.assertFalse(hasattr(self.protocol, 'encoder'))

    def test_decode_task(self):
        self.protocol.handshakeSuccess('')
        self.protocol._startDecoding()

        self.assertTrue(hasattr(self.protocol, 'decoder_task'))
        self.protocol.connectionLost(error.ConnectionDone())
        self.assertFalse(hasattr(self.protocol, 'decoder_task'))

    def test_encode_task(self):
        self.protocol.handshakeSuccess('')
        self.protocol._startEncoding()

        self.assertTrue(hasattr(self.protocol, 'encoder_task'))
        self.protocol.connectionLost(error.ConnectionDone())
        self.assertFalse(hasattr(self.protocol, 'encoder_task'))

    def test_inform_application(self):
        self.protocol.handshakeSuccess('')
        self.protocol.application = MockApplication(self, self.protocol)

        self.protocol.connectionLost(error.ConnectionDone())


class CooperateTestCase(ProtocolTestCase):
    """
    Tests for encoding/decoding cooperation
    """

    def setUp(self):
        ProtocolTestCase.setUp(self)

        self.connect()
        self.protocol.handshakeSuccess('')

    def test_fail_decode(self):
        def boom(*args):
            raise RuntimeError

        self.patch(self.protocol.decoder, 'next', boom)

        def eb(f):
            f.trap(RuntimeError)

            self.assertFalse(self.transport.connected)

        d = self.protocol._startDecoding().addErrback(eb)

        return d

    def test_fail_encode(self):
        def boom(*args):
            raise RuntimeError

        self.patch(self.protocol.encoder, 'next', boom)

        def eb(f):
            f.trap(RuntimeError)

            self.assertFalse(self.transport.connected)

        d = self.protocol._startEncoding().addErrback(eb)

        return d

    def test_resume_decode(self):
        d = self.protocol._startDecoding()

        def resume(res):
            self.assertTrue(self.transport.connected)
            return self.protocol._startDecoding()

        d.addCallback(resume)

        return d


class DataReceivedTestCase(ProtocolTestCase):
    """
    """

    def test_handshake(self):
        self.connect()
        self.assertEqual(self.protocol.state, 'handshake')

        self.protocol.dataReceived('woot')
        self.assertEqual(self.handshaker.data, 'woot')

        self.protocol.dataReceived('slartybartfarst')
        self.assertEqual(self.handshaker.data, 'wootslartybartfarst')

    def test_stream(self):
        self.connect()
        self.protocol.handshakeSuccess('')

        decoder = self.protocol.decoder

        self.assertEqual(self.protocol.decoder_task, None)
        self.protocol.dataReceived('woot')
        self.assertNotEqual(self.protocol.decoder_task, None)

        self.assertEqual(decoder.stream.getvalue(), 'woot')

        self.protocol.decoder_task.addErrback(lambda x: None)


class StreamTestCase(ProtocolTestCase):
    """
    Tests for L{rtmp.Stream}
    """

    def setUp(self):
        ProtocolTestCase.setUp(self)

        self.stream = rtmp.Stream(self.protocol, None)

    def test_create(self):
        """
        Ensure basic attribute initialisation and constructor args.
        """
        s = rtmp.Stream(self.protocol, 3)

        self.assertIdentical(s.protocol, self.protocol)
        self.assertEqual(s.streamId, 3)
        self.assertEqual(s.timestamp, 0)

    def test_set_timestamp(self):
        """
        @see: L{rtmp.Stream.setTimestamp}
        """
        self.assertEqual(self.stream.timestamp, 0)

        self.stream.setTimestamp(50)
        self.assertEqual(self.stream.timestamp, 50)

        self.stream.setTimestamp(50)
        self.assertEqual(self.stream.timestamp, 100)

        self.stream.setTimestamp(150, False)
        self.assertEqual(self.stream.timestamp, 150)

    def test_send_message(self):
        """
        Send a message back to the protocol.
        """
        self.executed = False

        def message(stream, message, whenDone):
            self.assertIdentical(self.stream, stream)
            self.assertEqual(message, 'foo')
            self.assertEqual(whenDone, 'bar')

            self.executed = True

        self.patch(self.protocol, 'sendMessage', message)

        self.stream.sendMessage('foo', 'bar')
        self.assertTrue(self.executed)


class ControlStreamTestCase(ProtocolTestCase):
    """
    Tests for L{rtmp.ControlStream}
    """

    def setUp(self):
        ProtocolTestCase.setUp(self)

        self.factory = MockFactory(self, self.protocol)
        self.protocol.factory = self.factory

        self.protocol.connectionMade()
        self.protocol.handshakeSuccess('')

        self.stream = rtmp.ControlStream(self.protocol, None)

    def test_create(self):
        """
        Ensure basic attribute initialisation and constructor args.
        """
        s = rtmp.ControlStream(self.protocol, 3)

        self.assertIdentical(s.protocol, self.protocol)
        self.assertEqual(s.streamId, 3)
        self.assertEqual(s.timestamp, 0)

        self.assertIdentical(s.decoder, self.protocol.decoder)
        self.assertIdentical(s.encoder, self.protocol.encoder)
