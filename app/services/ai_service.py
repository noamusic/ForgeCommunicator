"""
AI Agent Service for managing AI assistants and conversations.

Handles:
- Agent CRUD operations
- Conversation management
- Context building from workspace data
- Message handling with AI providers
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_agent import (
    AIAgent,
    AIAgentScope,
    AIConversation,
    AIMessage,
    AIChannelMembership,
    AIProvider,
)
from app.models.message import Message
from app.models.channel import Channel
from app.models.artifact import Artifact
from app.models.note import Note
from app.services.ai_providers import (
    get_provider,
    ChatMessage,
    ChatCompletionResponse,
    StreamChunk,
)

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


logger = logging.getLogger(__name__)


class AIAgentService:
    """Service for managing AI agents and conversations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # =========================================================================
    # Agent CRUD
    # =========================================================================
    
    async def create_agent(
        self,
        name: str,
        display_name: str,
        provider: AIProvider,
        api_key: str,
        model: str,
        scope: AIAgentScope = AIAgentScope.USER,
        workspace_id: int | None = None,
        owner_id: int | None = None,
        description: str | None = None,
        avatar_url: str | None = None,
        system_prompt: str | None = None,
        capabilities: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        context_messages: int = 20,
        can_read_channels: bool = False,
        can_read_dms: bool = False,
        can_read_notes: bool = False,
        can_read_artifacts: bool = False,
    ) -> AIAgent:
        """Create a new AI agent."""
        agent = AIAgent(
            name=name,
            display_name=display_name,
            provider=provider,
            api_key=api_key,  # TODO: encrypt this
            model=model,
            scope=scope,
            workspace_id=workspace_id,
            owner_id=owner_id,
            description=description,
            avatar_url=avatar_url,
            system_prompt=system_prompt,
            capabilities=capabilities or {},
            temperature=temperature,
            max_tokens=max_tokens,
            context_messages=context_messages,
            can_read_channels=can_read_channels,
            can_read_dms=can_read_dms,
            can_read_notes=can_read_notes,
            can_read_artifacts=can_read_artifacts,
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent
    
    async def get_agent(self, agent_id: int) -> AIAgent | None:
        """Get an agent by ID."""
        result = await self.db.execute(
            select(AIAgent).where(AIAgent.id == agent_id)
        )
        return result.scalar_one_or_none()
    
    async def get_workspace_agents(self, workspace_id: int) -> list[AIAgent]:
        """Get all agents for a workspace."""
        result = await self.db.execute(
            select(AIAgent)
            .where(
                and_(
                    AIAgent.workspace_id == workspace_id,
                    AIAgent.scope == AIAgentScope.WORKSPACE,
                    AIAgent.is_active == True,
                )
            )
            .order_by(AIAgent.name)
        )
        return list(result.scalars().all())
    
    async def get_user_agents(self, user_id: int) -> list[AIAgent]:
        """Get all personal agents for a user."""
        result = await self.db.execute(
            select(AIAgent)
            .where(
                and_(
                    AIAgent.owner_id == user_id,
                    AIAgent.scope == AIAgentScope.USER,
                    AIAgent.is_active == True,
                )
            )
            .order_by(AIAgent.name)
        )
        return list(result.scalars().all())
    
    async def get_available_agents(
        self, 
        user_id: int, 
        workspace_id: int | None = None,
    ) -> list[AIAgent]:
        """Get all agents available to a user (personal + workspace)."""
        conditions = [
            and_(AIAgent.owner_id == user_id, AIAgent.scope == AIAgentScope.USER),
        ]
        
        if workspace_id:
            conditions.append(
                and_(AIAgent.workspace_id == workspace_id, AIAgent.scope == AIAgentScope.WORKSPACE)
            )
        
        result = await self.db.execute(
            select(AIAgent)
            .where(and_(AIAgent.is_active == True, or_(*conditions)))
            .order_by(AIAgent.scope, AIAgent.name)
        )
        return list(result.scalars().all())
    
    async def update_agent(self, agent_id: int, **updates) -> AIAgent | None:
        """Update an agent's settings."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return None
        
        for key, value in updates.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        
        await self.db.commit()
        await self.db.refresh(agent)
        return agent
    
    async def delete_agent(self, agent_id: int) -> bool:
        """Delete an agent (soft delete by deactivating)."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return False
        
        agent.is_active = False
        await self.db.commit()
        return True
    
    # =========================================================================
    # Conversation Management
    # =========================================================================
    
    async def create_conversation(
        self,
        agent_id: int,
        user_id: int,
        channel_id: int | None = None,
        title: str | None = None,
    ) -> AIConversation:
        """Create a new conversation with an agent."""
        conversation = AIConversation(
            agent_id=agent_id,
            user_id=user_id,
            channel_id=channel_id,
            title=title,
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation
    
    async def get_conversation(self, conversation_id: int) -> AIConversation | None:
        """Get a conversation by ID."""
        result = await self.db.execute(
            select(AIConversation)
            .where(AIConversation.id == conversation_id)
            .options(selectinload(AIConversation.agent))
        )
        return result.scalar_one_or_none()
    
    async def get_user_conversations(
        self,
        user_id: int,
        agent_id: int | None = None,
        include_archived: bool = False,
    ) -> list[AIConversation]:
        """Get all conversations for a user."""
        conditions = [AIConversation.user_id == user_id]
        
        if agent_id:
            conditions.append(AIConversation.agent_id == agent_id)
        
        if not include_archived:
            conditions.append(AIConversation.is_archived == False)
        
        result = await self.db.execute(
            select(AIConversation)
            .where(and_(*conditions))
            .options(selectinload(AIConversation.agent))
            .order_by(AIConversation.updated_at.desc())
        )
        return list(result.scalars().all())
    
    async def get_or_create_conversation(
        self,
        agent_id: int,
        user_id: int,
        channel_id: int | None = None,
    ) -> AIConversation:
        """Get existing conversation or create a new one."""
        # For direct chats (no channel), find existing active conversation
        conditions = [
            AIConversation.agent_id == agent_id,
            AIConversation.user_id == user_id,
            AIConversation.is_archived == False,
        ]
        
        if channel_id:
            conditions.append(AIConversation.channel_id == channel_id)
        else:
            conditions.append(AIConversation.channel_id == None)
        
        result = await self.db.execute(
            select(AIConversation)
            .where(and_(*conditions))
            .options(selectinload(AIConversation.agent))
            .order_by(AIConversation.updated_at.desc())
            .limit(1)
        )
        conversation = result.scalar_one_or_none()
        
        if conversation:
            return conversation
        
        return await self.create_conversation(agent_id, user_id, channel_id)
    
    async def archive_conversation(self, conversation_id: int) -> bool:
        """Archive a conversation."""
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            return False
        
        conversation.is_archived = True
        await self.db.commit()
        return True
    
    # =========================================================================
    # Message Handling
    # =========================================================================
    
    async def get_conversation_messages(
        self,
        conversation_id: int,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[AIMessage]:
        """Get messages from a conversation."""
        conditions = [AIMessage.conversation_id == conversation_id]
        
        if before_id:
            conditions.append(AIMessage.id < before_id)
        
        result = await self.db.execute(
            select(AIMessage)
            .where(and_(*conditions))
            .order_by(AIMessage.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()  # Return in chronological order
        return messages
    
    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        model_used: str | None = None,
        referenced_message_ids: list[int] | None = None,
    ) -> AIMessage:
        """Add a message to a conversation."""
        message = AIMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_used=model_used,
            referenced_message_ids=referenced_message_ids,
        )
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message
    
    # =========================================================================
    # Context Building
    # =========================================================================
    
    async def build_context(
        self,
        agent: AIAgent,
        workspace_id: int,
        channel_id: int | None = None,
        include_recent_messages: bool = True,
        message_limit: int = 50,
    ) -> str:
        """Build context string from workspace data for the AI."""
        context_parts = []
        
        # Get recent channel messages if allowed and channel specified
        if agent.can_read_channels and channel_id and include_recent_messages:
            messages = await self._get_channel_messages(channel_id, message_limit)
            if messages:
                context_parts.append("## Recent Channel Messages\n")
                for msg in messages:
                    author = msg.user.display_name if msg.user else "Unknown"
                    context_parts.append(f"**{author}**: {msg.body}")
                context_parts.append("")
        
        # Get artifacts if allowed
        if agent.can_read_artifacts:
            artifacts = await self._get_workspace_artifacts(workspace_id, channel_id)
            if artifacts:
                context_parts.append("## Workspace Artifacts (Tasks, Decisions, Ideas)\n")
                for artifact in artifacts:
                    context_parts.append(
                        f"- [{artifact.type.upper()}] {artifact.title} ({artifact.status})"
                    )
                context_parts.append("")
        
        # Get notes if allowed (workspace-visible notes)
        if agent.can_read_notes:
            notes = await self._get_workspace_notes(workspace_id)
            if notes:
                context_parts.append("## Workspace Notes\n")
                for note in notes[:10]:  # Limit to prevent context overflow
                    context_parts.append(f"### {note.title}\n{note.content[:500]}...")
                context_parts.append("")
        
        return "\n".join(context_parts)
    
    async def _get_channel_messages(
        self,
        channel_id: int,
        limit: int = 50,
    ) -> list[Message]:
        """Get recent messages from a channel."""
        result = await self.db.execute(
            select(Message)
            .where(
                and_(
                    Message.channel_id == channel_id,
                    Message.deleted_at == None,
                )
            )
            .options(selectinload(Message.user))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages
    
    async def _get_workspace_artifacts(
        self,
        workspace_id: int,
        channel_id: int | None = None,
    ) -> list[Artifact]:
        """Get artifacts from workspace or channel."""
        # Get channels in workspace
        channel_result = await self.db.execute(
            select(Channel.id).where(Channel.workspace_id == workspace_id)
        )
        channel_ids = [r[0] for r in channel_result.fetchall()]
        
        if channel_id:
            channel_ids = [channel_id]
        
        if not channel_ids:
            return []
        
        result = await self.db.execute(
            select(Artifact)
            .where(Artifact.channel_id.in_(channel_ids))
            .order_by(Artifact.updated_at.desc())
            .limit(50)
        )
        return list(result.scalars().all())
    
    async def _get_workspace_notes(self, workspace_id: int) -> list[Note]:
        """Get workspace-visible notes."""
        from app.models.note import NoteVisibility
        
        result = await self.db.execute(
            select(Note)
            .where(
                and_(
                    Note.workspace_id == workspace_id,
                    Note.visibility == NoteVisibility.WORKSPACE,
                )
            )
            .order_by(Note.updated_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())
    
    # =========================================================================
    # Chat Completion
    # =========================================================================
    
    async def send_message(
        self,
        conversation_id: int,
        user_message: str,
        workspace_id: int | None = None,
        channel_id: int | None = None,
    ) -> AIMessage:
        """Send a message and get AI response."""
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")
        
        agent = conversation.agent
        
        # Save user message
        await self.add_message(conversation_id, "user", user_message)
        
        # Build message history
        history = await self.get_conversation_messages(
            conversation_id, limit=agent.context_messages
        )
        
        # Build context from workspace data
        context = ""
        if workspace_id and any([
            agent.can_read_channels,
            agent.can_read_artifacts,
            agent.can_read_notes,
        ]):
            context = await self.build_context(
                agent, workspace_id, channel_id
            )
        
        # Prepare messages for AI
        messages = []
        
        # System prompt with context
        system_prompt = agent.system_prompt or f"You are {agent.display_name}, a helpful AI assistant."
        if context:
            system_prompt += f"\n\n## Workspace Context\n{context}"
        messages.append(ChatMessage(role="system", content=system_prompt))
        
        # Add conversation history
        for msg in history:
            messages.append(ChatMessage(role=msg.role, content=msg.content))
        
        # Get AI response
        provider = get_provider(
            agent.provider,
            agent.api_key,
            agent.model,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
        )
        
        try:
            response = await provider.chat(messages)
            
            # Save assistant response
            ai_message = await self.add_message(
                conversation_id,
                "assistant",
                response.content,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                model_used=response.model,
            )
            
            # Update agent usage stats
            if response.total_tokens:
                agent.update_usage(response.total_tokens)
                await self.db.commit()
            
            # Auto-generate title if first message
            if not conversation.title and len(history) <= 2:
                conversation.title = user_message[:100]
                await self.db.commit()
            
            return ai_message
            
        except Exception as e:
            logger.error(f"AI request failed: {e}")
            # Save error as message
            error_message = await self.add_message(
                conversation_id,
                "assistant",
                f"I encountered an error: {str(e)}. Please try again.",
            )
            return error_message
    
    # =========================================================================
    # Channel Membership
    # =========================================================================
    
    async def add_agent_to_channel(
        self,
        agent_id: int,
        channel_id: int,
        added_by_id: int,
        respond_to_mentions: bool = True,
        respond_to_all: bool = False,
        auto_summarize: bool = False,
    ) -> AIChannelMembership:
        """Add an AI agent to a channel."""
        membership = AIChannelMembership(
            agent_id=agent_id,
            channel_id=channel_id,
            added_by_id=added_by_id,
            respond_to_mentions=respond_to_mentions,
            respond_to_all=respond_to_all,
            auto_summarize=auto_summarize,
        )
        self.db.add(membership)
        await self.db.commit()
        await self.db.refresh(membership)
        return membership
    
    async def remove_agent_from_channel(
        self,
        agent_id: int,
        channel_id: int,
    ) -> bool:
        """Remove an AI agent from a channel."""
        result = await self.db.execute(
            select(AIChannelMembership)
            .where(
                and_(
                    AIChannelMembership.agent_id == agent_id,
                    AIChannelMembership.channel_id == channel_id,
                )
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            return False
        
        await self.db.delete(membership)
        await self.db.commit()
        return True
    
    async def get_channel_agents(self, channel_id: int) -> list[AIChannelMembership]:
        """Get all AI agents in a channel."""
        result = await self.db.execute(
            select(AIChannelMembership)
            .where(AIChannelMembership.channel_id == channel_id)
            .options(selectinload(AIChannelMembership.agent))
        )
        return list(result.scalars().all())
