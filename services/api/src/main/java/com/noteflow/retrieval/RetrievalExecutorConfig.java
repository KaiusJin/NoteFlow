package com.noteflow.retrieval;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
class RetrievalExecutorConfig {
    /**
     * Retrieval fan-out is a small number of purely I/O-bound calls per request
     * (vector/lexical/exact recall plus external rerank/HyDE). Virtual threads
     * remove the fixed-pool queueing bottleneck; downstream pressure is bounded
     * by the Hikari connection pool and external client timeouts.
     */
    @Bean(name = "retrievalExecutor", destroyMethod = "close")
    ExecutorService retrievalExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }
}
